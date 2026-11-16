"""pytest-verifier adapter: its checks become "Verify …" lines in their steps.

pytest-verifier (the ``verify`` fixture) judges checks and passes them, at the
end of each test phase, to its ``pytest_verify_results`` hook as JSON-safe
descriptors. This adapter places each one in the step it was made in, using the
descriptor's ``location`` (and ``called_from`` for checks made in helpers), and
renders expected values given by name symbolically in the procedure
(``'Stored age' == <age>``).

Without this adapter, stepdoc cannot see verify's checks, so no step of a test
that uses verify is shown as passed (NFR-7).
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterator, Mapping, Optional, Sequence

import pytest

from ..core.model import Action, Check, Location, Repeat, Section, Step
from ..core.recorder import Recorder
from ..core.symbolic import target_pattern
from . import Adapter, Host
from ._source import argument, calls_at, same_file, source_text, statement_span, symbolic

_SECTION: dict[str, Section] = {"setup": "setup", "call": "procedure", "teardown": "teardown"}

# check_type -> (operator, argument position, keyword) of the expected value.
_COMPARISONS: dict[str, tuple[str, int, str]] = {
    "equal": ("==", 1, "expected"),
    "not_equal": ("!=", 1, "expected"),
    "approx": ("==", 1, "expected"),
    "greater": (">", 1, "threshold"),
    "greater_equal": (">=", 1, "threshold"),
    "less": ("<", 1, "threshold"),
    "less_equal": ("<=", 1, "threshold"),
    "contains": ("contains", 1, "needle"),
    "not_contains": ("does not contain", 1, "needle"),
    "length": ("has length", 1, "expected"),
}


class VerifyAdapter(Adapter):
    name = "verify"
    plugins = ("pytest_verifier",)
    judges = True

    def uses(self, item: pytest.Item) -> bool:
        return "verify" in getattr(item, "fixturenames", ())

    def activate(self, host: Host) -> object:
        return _Plugin(host)


class _Plugin:
    def __init__(self, host: Host) -> None:
        self.host = host

    @pytest.hookimpl(optionalhook=True)
    def pytest_verify_results(
        self, item: pytest.Item, when: str, checks: Sequence[Mapping[str, Any]], passed: bool
    ) -> None:
        rec = self.host.recorder(item)
        if rec is None:
            return
        rec.resolve()  # action locations order the checks among the actions
        placer = _Placer(rec, self.host.root)
        for d in checks:
            placer.place(d, _SECTION.get(str(d.get("phase") or when), "procedure"))
        rec.resolve_notes()


def _location(text: Any, root: str) -> Optional[Location]:
    """``"tests/test_x.py:12"`` (relative to the rootdir) as a Location."""
    if not isinstance(text, str) or ":" not in text:
        return None
    where = _where(text, root)
    return None if where is None else Location(*where)


@lru_cache(maxsize=4096)
def _where(text: str, root: str) -> Optional[tuple[str, int]]:
    path, _, line = text.rpartition(":")
    if not line.isdigit():
        return None
    return os.path.join(root, path), int(line)


# check_type -> the Verify method that makes it, where the names differ.
_METHODS: dict[str, str] = {"true": "is_true", "false": "is_false"}

# Check types whose first argument is the checked value: their text starts with
# it (``dat.txfe == 1``) and reads made on their line fold into them (ACT-5).
_SUBJECTS = frozenset(
    {
        "equal", "not_equal", "approx", "greater", "greater_equal", "less", "less_equal",
        "between", "true", "false", "is_none", "is_not_none",
    }
)


def _note(d: Mapping[str, Any]) -> Optional[str]:
    note = d.get("note")
    return note.strip() if isinstance(note, str) and note.strip() else None


def to_check(d: Mapping[str, Any], root: str) -> Check:
    """A verify check descriptor as a stepdoc Check."""
    return _build(d, root)[0]


def _build(d: Mapping[str, Any], root: str) -> tuple[Check, Optional["_Wording"]]:
    description = str(d.get("description") or d.get("name") or d.get("check_type") or "check")
    text = description[len("Verify "):] if description.startswith("Verify ") else description
    passed = d.get("passed")
    location = _location(d.get("location"), root)
    selected = d.get("matched_case")
    if selected is None and isinstance(d.get("matched_index"), int):
        branches = d.get("branches") or []
        index = d["matched_index"]
        if 0 <= index < len(branches):
            selected = branches[index].get("label")
    rest = _rest(description, d.get("name"))
    hint = _hint(d)
    sym_rest = _symbolic_rest(d, rest, location, hint) if rest is not None else None
    check = Check(
        text=text,
        passed=passed if isinstance(passed, bool) else None,
        expected=d.get("expected"),
        actual=d.get("actual"),
        detail=None if passed is True else (d.get("error") or d.get("detail")),
        children=[to_check(c, root) for c in d.get("child_checks") or [] if isinstance(c, Mapping)],
        selected=None if selected is None else str(selected),
        location=location,
        source=d,
        symbolic_text=None if sym_rest is None else f"'{d.get('name')}' {sym_rest}",
        note=_note(d),
    )
    wording = None
    kind = str(d.get("check_type"))
    if rest is not None and location is not None and kind in _SUBJECTS:
        subject = _subject(location.file, location.line, _METHODS.get(kind, kind), hint)
        if subject is not None:
            prefix = "" if d.get("auto_name") is True else f"'{d.get('name')}': "
            wording = _Wording(prefix, rest, sym_rest, subject)
            wording.apply(check, subject.symbolic, subject.concrete)
    return check, wording


def _rest(description: str, name: Any) -> Optional[str]:
    """What follows ``Verify '<name>' `` in a description (``== 1``, ``is True``),
    or None when the description has another shape."""
    prefix = f"Verify '{name}' "
    if description.startswith(prefix):
        return description[len(prefix):] or None
    m = _DESCRIPTION.match(description)
    return m.group(1) if m else None


_DESCRIPTION = re.compile(r"Verify '.*?' ((?:==|!=|>=|<=|>|<|∈|is) .*)\Z", re.DOTALL)


@dataclass(frozen=True)
class _Subject:
    """The checked value's argument at a call site."""

    key: str
    """Its expression unparsed, to find the read that produced it."""
    symbolic: str
    """For the procedure: as written, with the variable parts of ``getattr``
    names and subscripts as ``<expr>`` (aliases are not followed, so both
    documents name the same expression)."""
    concrete: str
    """For the report: as written."""
    span: tuple[int, int]
    """First and last line of the call."""
    name: Optional[str] = None
    """The variable, when the checked value is a bare name (``v``)."""
    exprs: frozenset[str] = frozenset()
    """Every expression in the call, unparsed: the reads it may absorb."""


def _hint(d: Mapping[str, Any]) -> Optional[str]:
    """The checked value's source text as pytest-verifier gave it (``auto_name``)."""
    name = d.get("name")
    return name if d.get("auto_name") is True and isinstance(name, str) else None


def _first(call: ast.Call) -> Optional[ast.expr]:
    """The checked value's argument: the first positional one, or ``actual=``/``haystack=``."""
    if call.args:
        return call.args[0]
    return next((kw.value for kw in call.keywords if kw.arg in ("actual", "haystack")), None)


@lru_cache(maxsize=4096)
def _call(path: str, line: int, method: str, hint: Optional[str]) -> Optional[ast.Call]:
    """The ``.method(...)`` call that made a check at ``path:line``. With several
    on the line, the one whose checked value reads ``hint``; None when that does
    not tell them apart (the check then keeps its plain text)."""
    calls = calls_at(path, line, method)
    if len(calls) == 1:
        return calls[0]
    if hint is None:
        return None
    matches = [c for c in calls if _reads_as(path, _first(c), hint)]
    return matches[0] if len(matches) == 1 else None


def _reads_as(path: str, node: Optional[ast.expr], hint: str) -> bool:
    if node is None or isinstance(node, ast.Starred):
        return False
    texts = {" ".join(ast.unparse(node).split()), source_text(path, node)}
    if hint in texts:
        return True
    return hint.endswith("...") and any(t and t.startswith(hint[:-3]) for t in texts)


@lru_cache(maxsize=4096)
def _subject(path: str, line: int, method: str, hint: Optional[str] = None) -> Optional[_Subject]:
    """The first argument of the ``.method(...)`` call at ``path:line``; None when
    it is a literal, the call is ambiguous or the source is not available. Never
    raises."""
    try:
        call = _call(path, line, method, hint)
        node = _first(call) if call is not None else None
        if call is None or node is None or isinstance(node, ast.Starred) or symbolic(path, node) is None:
            return None
        written = source_text(path, node) or ast.unparse(node)
        return _Subject(
            key=ast.unparse(node),
            symbolic=target_pattern(node, aliases=False) or written,
            concrete=written,
            span=(call.lineno, call.end_lineno or call.lineno),
            name=node.id if isinstance(node, ast.Name) else None,
            exprs=frozenset(ast.unparse(n) for n in ast.walk(call) if isinstance(n, ast.expr)),
        )
    except Exception:  # pragma: no cover - defensive: documents never break a run
        return None


@dataclass
class _Wording:
    """How a check with a known subject reads: ``[prefix]<subject> <rest>``."""

    prefix: str
    rest: str
    sym_rest: Optional[str]
    subject: _Subject

    def apply(self, check: Check, symbolic: str, concrete: str) -> None:
        check.text = f"{self.prefix}{concrete} {self.rest}"
        sym = f"{self.prefix}{symbolic} {self.sym_rest or self.rest}"
        check.symbolic_text = None if sym == check.text else sym


def _symbolic_rest(
    d: Mapping[str, Any], rest: str, loc: Optional[Location], hint: Optional[str] = None
) -> Optional[str]:
    """The text after the name for the procedure when the expected value is a name
    or an expression (``== <age>``); None when it is a literal (``rest`` already
    reads right)."""
    kind = str(d.get("check_type"))
    if loc is None or (kind != "between" and kind not in _COMPARISONS):
        return None
    syms = _expected(loc.file, loc.line, kind, hint)
    if syms is None:
        return None
    units = f" {d['units']}" if d.get("units") else ""
    if kind == "between":
        lo, hi = syms
        bounds = rest[rest.index("∈") + 1:].strip() if "∈" in rest else ""
        concrete = [b.strip() for b in bounds[1:-1].split(",")] if bounds[:1] in "[(" else []
        if len(concrete) != 2:
            return None
        lo_text, hi_text = (lo + units if lo else concrete[0]), (hi + units if hi else concrete[1])
        return f"∈ {bounds[0]}{lo_text}, {hi_text}{bounds[-1]}"
    op = _COMPARISONS[kind][0]
    tolerance = rest[rest.index(" ±"):] if kind == "approx" and " ±" in rest else ""
    return f"{op} {syms[0]}{units}{tolerance}"


@lru_cache(maxsize=4096)
def _expected(
    path: str, line: int, kind: str, hint: Optional[str] = None
) -> Optional[tuple[Optional[str], ...]]:
    """The expected value's ``<source>`` at a call site (``(low, high)`` for
    ``between``), once per line; None when every one is a literal."""
    call = _call(path, line, _METHODS.get(kind, kind), hint)
    if call is None:
        return None
    if kind == "between":
        low, high = argument(call, 1, "low"), argument(call, 2, "high")
        lo = symbolic(path, low) if low is not None else None
        hi = symbolic(path, high) if high is not None else None
        return None if lo is None and hi is None else (lo, hi)
    _, position, keyword = _COMPARISONS[kind]
    node = argument(call, position, keyword)
    sym = symbolic(path, node) if node is not None else None
    return None if sym is None else (sym,)


class _Placer:
    """Puts checks that arrive after a phase into the steps that were open when
    they were made. Steps are matched by source lines: a check belongs to the
    innermost step whose ``with`` block (or decorated call) contains the line
    that made it. Checks are in the order they were made, so a step that ran
    several times (a loop) takes them in turn."""

    def __init__(self, rec: Recorder, root: str) -> None:
        self.rec = rec
        self.root = root
        self.paths: list[tuple[Step, ...]] = []
        self._walk(rec.steps, ())
        self.at: dict[int, int] = {id(p[-1]): i for i, p in enumerate(self.paths)}
        self.spans: dict[int, Optional[tuple[str, int, int]]] = {}
        self.cursor = -1
        self.seen: dict[int, set[tuple[str, int]]] = {}
        self.next_index: dict[int, int] = {}
        self.reuse: dict[tuple[int, str, int], tuple[int, tuple[str, str]]] = {}
        """(step, file, line) -> how many more checks from there take the wording
        of a read already absorbed (collapsed polling: ``count`` reads of one
        target, one per check) without folding another, and that wording
        (symbolic and concrete target)."""

    def _walk(self, steps: Sequence[Step], parent: tuple[Step, ...]) -> None:
        for s in steps:
            path = parent + (s,)
            self.paths.append(path)
            self._walk(s.children, path)

    def _contains(self, i: int, anchors: list[tuple[str, int]]) -> bool:
        if i not in self.spans:
            loc = self.paths[i][-1].location
            if loc is None:
                self.spans[i] = None
            else:
                first, last = statement_span(loc.file, loc.line)
                self.spans[i] = (_norm(loc.file), first, last)
        span = self.spans[i]
        return span is not None and any(f == span[0] and span[1] <= n <= span[2] for f, n in anchors)

    def _candidates(self) -> Iterator[int]:
        """Paths a check may go to, in order: the last match's ancestors and the
        last match itself, then every path after it (checks come in the order they
        were made, so they never go back to an earlier step)."""
        if self.cursor < 0:
            yield from range(len(self.paths))
            return
        for s in self.paths[self.cursor][:-1]:
            yield self.at[id(s)]
        yield from range(self.cursor, len(self.paths))

    def place(self, d: Mapping[str, Any], section: Section) -> None:
        check, wording = _build(d, self.root)
        if check.note is None:
            check.note_from_comment = True  # looked up when the recorder resolves
        anchors = [a for a in (check.location, _location(d.get("called_from"), self.root)) if a]
        lines = [(_norm(a.file), a.line) for a in anchors]
        best: Optional[int] = None
        for i in self._candidates():
            path = self.paths[i]
            if path[-1].section != section or not self._contains(i, lines):
                continue
            if best is not None and self.paths[best] != path[: len(self.paths[best])]:
                # Not nested in the match so far: a later run of the same step.
                key = (anchors[0].file, anchors[0].line)
                if key not in self.seen.get(best, set()):
                    break  # ``best`` has not taken a check from this line yet
                best = i
                continue
            best = i  # nested deeper (or the first match)
        step = -1 if best is None else best
        slot: Optional[tuple[int, str, int]] = None
        fold = None
        if wording is not None:
            assert check.location is not None
            slot = (step, _norm(check.location.file), wording.subject.span[0])
            if self.reuse.get(slot, (0, ("", "")))[0] <= 0:
                fold = wording.subject.span  # else: this pass's read went to an earlier check
        start = self.next_index.get(step, 0)
        exprs = wording.subject.exprs if wording is not None else None
        if best is None:
            check.section = section
            entries: list[Any] = self.rec.unscoped
            at = self.rec.insert_check((), check, fold=fold, start=start, fold_exprs=exprs)
        else:
            self.cursor = best
            if anchors:
                self.seen.setdefault(best, set()).add((anchors[0].file, anchors[0].line))
            path = self.paths[best]
            entries = path[-1].entries
            if wording is not None and fold is None:
                index = start  # the next pass of a polled read: right after the last
            else:
                index = self._index(best, path[-1], anchors, wording.subject.span if wording else None)
            at = self.rec.insert_check(path, check, index, fold=fold, start=start, fold_exprs=exprs)
        self.next_index[step] = at + 1
        if wording is not None and slot is not None:
            self._reword(check, wording, slot, entries, start, at)

    def _reword(
        self,
        check: Check,
        wording: _Wording,
        key: tuple[int, str, int],
        entries: list[Any],
        start: int,
        at: int,
    ) -> None:
        """Name the checked value after the read that produced it (a register:
        ``dev.blk.reg<ch>.ready``), only when that read provably belongs to this
        check's pass: one it absorbed; for the next ``count - 1`` checks from
        the same line, a polled read (``count`` > 1) an earlier check absorbed; a
        read in a collapsed loop just before it (every pass reads the same
        target); the one read a bare variable was bound to. Otherwise the check
        keeps the argument's source text."""
        loc = check.location
        assert loc is not None
        subject = wording.subject
        read = next((a for a in check.reads if a.read_expr == subject.key), None)
        if read is not None:
            found = _targets(read)
            self.reuse[key] = (read.count - 1, found)
            wording.apply(check, *found)
            return
        left, found = self.reuse.get(key, (0, ("", "")))
        if left > 0:
            self.reuse[key] = (left - 1, found)
            wording.apply(check, *found)
            return
        for e in entries[start:at]:
            if isinstance(e, Repeat):
                read = next(
                    (a for a in _reads(e.entries) if a.read_expr == subject.key and _on(a, loc.file, subject.span)),
                    None,
                )
                if read is not None:
                    found = _targets(read)
                    passes = e.count if isinstance(e.count, int) else 1
                    self.reuse[key] = (passes * read.count - 1, found)
                    wording.apply(check, *found)
                    return
        if subject.name is not None:
            bound = {_targets(a) for a in _reads(entries) if a.bound_to == subject.name}
            if len(bound) == 1:
                wording.apply(check, *bound.pop())

    def _index(
        self, i: int, s: Step, anchors: list[Location], span: Optional[tuple[int, int]] = None
    ) -> int:
        """Where the check goes among the step's entries: before the first entry,
        from the last insertion on, made on a later line of the same file (after
        the call's last line, when the call spans several). The line is the
        anchor inside the step's block: the check's own, else the line of the
        test that called the helper that made it."""
        start = self.next_index.get(i, 0)
        same = [a for a in anchors if s.location and same_file(a.file, s.location.file)]
        span_i = self.spans.get(i)
        inside = [a for a in same if span_i is not None and span_i[1] <= a.line <= span_i[2]]
        anchor = inside[0] if inside else (same[0] if same else None)
        if anchor is None:
            return len(s.entries)
        after = anchor.line
        if span is not None and anchor is anchors[0] and span[0] <= anchor.line <= span[1]:
            after = span[1]
        for j in range(start, len(s.entries)):
            loc = s.entries[j].location
            if loc is not None and same_file(loc.file, anchor.file) and loc.line > after:
                return j
        return len(s.entries)


def _targets(read: Action) -> tuple[str, str]:
    return (read.symbolic_target or read.target, read.target)


def _reads(entries: Sequence[Any]) -> Iterator[Action]:
    """Results among ``entries``, also inside collapsed loops and checks' reads
    (not in child steps)."""
    for e in entries:
        if isinstance(e, Action):
            if e.direction == "out":
                yield e
        elif isinstance(e, Check):
            yield from e.reads
        elif isinstance(e, Repeat):
            yield from _reads(e.entries)


def _on(a: Action, path: str, span: tuple[int, int]) -> bool:
    loc = a.location
    return loc is not None and span[0] <= loc.line <= span[1] and same_file(loc.file, path)


@lru_cache(maxsize=1024)
def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))
