"""pytest-verifier adapter: its checks become "Verify …" lines in their steps.

pytest-verifier (the ``verify`` fixture) judges checks and passes them, at the
end of each test phase, to its ``pytest_verify_results`` hook as JSON-safe
descriptors. This adapter places each one in the step it was made in, using the
descriptor's ``location`` (and ``called_from`` for checks made in helpers), and
renders expected values given by name symbolically in the procedure
(``'Stored age' == <age>``). Operand names given to a check (``actual_name=``,
``expected_name=``) word it in place of what the source says: their generic form
in the procedure (``tb.dev.map.block.reg<channel>.F == <wdata>``), this run's in
the report (``tb.dev.map.block.reg3.F == wdata (243)``).

A check that shows its values in a format (``radix=``, ``width=``, ``bits``, a
value with ``__verify_format__``) carries pytest-verifier's own text of the value
it got (``0x00F0``) and of the bits that differ, which the report shows in place
of stepdoc's rendering: ``-> got 0x00F0, bits [1:0] differ``.

Without this adapter, stepdoc cannot see verify's checks, so no step of a test
that uses verify is shown as passed (NFR-7).
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Iterator, Mapping, Optional, Sequence

import pytest

from ..core.model import EACH_VALUES, Action, Check, Each, Location, Repeat, Section, Step
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
        "between", "true", "false", "is_none", "is_not_none", "bits",
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
    kind = str(d.get("check_type"))
    rest = _rest(description, d.get("name"))
    actual = _operand(d, "actual_name") if rest is not None else None
    # With an operand name the automatic name is that name, not the source text:
    # calls sharing a line are then told apart by their ``actual_name=``.
    hint = _hint(d) if actual is None else None
    spelled = None if actual is None else actual["concrete"]
    expected_name = None
    sym_rest = None
    if rest is not None and kind == "bits":
        expected_name, sym_rest = _bits_rest(d, rest, location, hint, spelled)
    elif rest is not None:
        expected = _named_expected(d, rest)
        if expected is not None:
            expected_name, sym_rest = expected
        else:
            sym_rest = _symbolic_rest(d, rest, location, hint, spelled)
    operands = {k: v for k, v in (("actual", actual), ("expected", expected_name)) if v}
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
        operands=operands or None,
        actual_text=_actual_text(d),
        difference=_difference(d),
    )
    wording = None
    prefix = "" if d.get("auto_name") is True else f"'{d.get('name')}': "
    named = None if actual is None else (actual["symbolic"], actual["concrete"])
    if rest is not None and location is not None and kind in _SUBJECTS:
        subject = _subject(location.file, location.line, _METHODS.get(kind, kind), hint, spelled)
        if subject is not None and kind == "bits":
            subject = _grouped(subject, masked=rest.startswith("& "))
        if subject is not None:
            wording = _Wording(prefix, rest, sym_rest, subject, named)
            wording.apply(check, subject.symbolic, subject.concrete)
    if wording is None and named is not None and rest is not None:
        _word(check, prefix, named[0], named[1], rest, sym_rest)
    name = d.get("name")
    if isinstance(name, str) and name and d.get("auto_name") is not True:
        if check.text.startswith((f"'{name}': ", f"'{name}' ")):
            check.label = name
    if kind == "all_satisfy":
        _fold_each(check, d)
    return check, wording


def _actual_text(d: Mapping[str, Any]) -> Optional[str]:
    """pytest-verifier's own text of the value a check got, when the check shows
    its values in a format (``rendered["actual"]``: ``0x00F0``); else None."""
    rendered = d.get("rendered")
    return _line(rendered.get("actual")) if isinstance(rendered, Mapping) else None


_FIELDS_SHOWN = 6
"""Most differing fields a ``difference`` names, as pytest-verifier's detail does."""


def _difference(d: Mapping[str, Any]) -> Optional[str]:
    """What differs in a failed check, from the record's ``bit_diff``: the clause
    pytest-verifier's detail ends with, ``bits [1:0] differ: MODE [3:1] is 0b000,
    expected 0b001``. None without one, or when it has another shape."""
    diff = d.get("bit_diff")
    text = _line(diff.get("text")) if isinstance(diff, Mapping) else None
    if text is None:
        return None
    assert isinstance(diff, Mapping)
    fields = diff.get("fields")
    parts = []
    for f in fields if isinstance(fields, (list, tuple)) else ():
        if not isinstance(f, Mapping):
            return text
        name, got, want = _line(f.get("name")), _line(f.get("actual")), _line(f.get("expected"))
        msb, lsb = f.get("msb"), f.get("lsb")
        if None in (name, got, want) or not _is_int(msb) or not _is_int(lsb):
            return text
        span = f"[{msb}]" if msb == lsb else f"[{msb}:{lsb}]"
        parts.append(f"{name} {span} is {got}, expected {want}")
    more = diff.get("more_fields")
    hidden = len(parts) - _FIELDS_SHOWN + (more if isinstance(more, int) and more > 0 else 0)
    parts = parts[:_FIELDS_SHOWN]
    if parts and hidden > 0:
        parts.append(f"and {hidden} more")
    return f"{text}: {'; '.join(parts)}" if parts else text


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _fold_each(check: Check, d: Mapping[str, Any]) -> None:
    """One line for an ``all_satisfy`` whose children all read the same (CHK-11):
    ``'Index error': for each w in waveforms, w & INDEX_ERROR_BIT == 0``. Only the
    failed children stay, each with its ``index``. Children of different kinds,
    names or code, composite children, and a check that could not make them all
    (``error``) are left as they are."""
    kids = check.children
    raw = [c for c in d.get("child_checks") or [] if isinstance(c, Mapping)]
    if not kids or len(raw) != len(kids) or d.get("error") is not None:
        return
    if len({str(c.get("check_type")) for c in raw}) != 1 or any(k.children for k in kids):
        return
    shapes = {(k.label, _item_code(k, True)) for k in kids}
    if len(shapes) != 1:
        return
    symbolic = shapes.pop()[1]
    concrete = {_item_code(k, False) for k in kids}
    item, items = _line(d.get("item_name")), _line(d.get("items_name"))
    lead = f"for each {item or 'item'}" + (f" in {items}" if items else "") + ", "
    name = _line(d.get("name")) or "all items"
    head = f"'{name}': "
    check.text = head + lead + (concrete.pop() if len(concrete) == 1 else symbolic)
    sym_text = head + lead + symbolic
    check.symbolic_text = None if sym_text == check.text else sym_text
    check.label = name
    check.operands = None
    notes = {k.note for k in kids}
    if check.note is None and len(notes) == 1:
        check.note = notes.pop()  # the one note every item's check carries
    failed = [k for k in kids if k.passed is False]
    for i, k in enumerate(kids):
        k.index = i
    half = EACH_VALUES // 2
    shown = kids if len(kids) <= EACH_VALUES else kids[:half] + kids[-half:]
    check.each = Each(
        count=len(kids),
        failed=len(failed),
        item=item,
        items=items,
        values=[k.actual for k in shown],
        texts=[k.actual_text for k in shown] if any(k.actual_text for k in shown) else None,
    )
    check.children = failed


def _item_code(c: Check, symbolic: bool) -> str:
    """The text of one item's check without its own name, which the folded line
    replaces with the composite's: ``w & INDEX_ERROR_BIT == 0``. A name that is the
    subject itself (``'Vout' == 3.3``) stays."""
    text = (c.symbolic_text or c.text) if symbolic else c.text
    if c.label and text.startswith(f"'{c.label}': "):
        return text[len(c.label) + 4:]
    return text


# Check types that take ``expected_name=``: all of them are in _COMPARISONS.
_NAMED_EXPECTED = frozenset(
    {"equal", "not_equal", "approx", "greater", "greater_equal", "less", "less_equal", "length"}
)
# ``<channel>``, ``<i + 1>``: a variable part written in a name, not a comparison
# (``a < b > c``).
_PLACEHOLDER = re.compile(r"<[^<>\s](?:[^<>]*[^<>\s])?>")
# Control characters, line and paragraph separators and lone surrogates: a name
# is one line of text that every document can hold.
_UNSAFE = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]+")


def _line(text: Any) -> Optional[str]:
    """``text`` as one line (control characters and line breaks as a space),
    stripped; None when it is not text or is blank."""
    if not isinstance(text, str):
        return None
    return _UNSAFE.sub(" ", text).strip() or None


def _operand(d: Mapping[str, Any], key: str) -> Optional[dict[str, str]]:
    """An operand name of a check (pytest-verifier's ``actual_name=`` or
    ``expected_name=``) as ``{"concrete": ..., "symbolic": ...}``: the text with
    this run's values and its generic form (each interpolation as ``<expr>``; the
    same text when the name has none or pytest-verifier does not know them), each
    as one line. None when the record has no such name."""
    name = _line(d.get(key))
    if name is None:
        return None
    return {"concrete": name, "symbolic": _line(d.get(f"{key}_symbolic")) or name}


def _named_expected(d: Mapping[str, Any], rest: str) -> Optional[tuple[dict[str, str], str]]:
    """The expected value's operand name and the text after the checked value for
    the procedure: the generic name alone, no value of this run (``== <wdata>``;
    a name with ``<parts>`` of its own as it is, ``== limits[<channel>]``).

    pytest-verifier's description has the name before the value (``== wdata
    (243)``, ``== nominal (3.3V) ± 0.05V``); the procedure's text is built from
    the record's name, so it holds no run value even when the description shows
    the name another way (shortened, escaped). None when the check has no such
    name, or is an ``approx`` whose tolerance cannot be told from the description:
    the check is then worded as without the name."""
    kind = str(d.get("check_type"))
    name = _operand(d, "expected_name") if kind in _NAMED_EXPECTED else None
    if name is None:
        return None
    op = _COMPARISONS[kind][0]
    tolerance = "" if kind != "approx" else _tolerance(d, rest, f"{op} {name['concrete']} (")
    if tolerance is None:
        return None
    units = f" {d['units']}" if d.get("units") else ""
    return name, f"{op} {_generic(name)}{units}{tolerance}"


def _generic(name: Mapping[str, str]) -> str:
    """An expected value's operand name as the procedure shows it: ``<wdata>``; a
    name with variable parts (the ones pytest-verifier found, or ``<parts>``
    written in it) as it is, ``limits[<channel>]``."""
    generic = name["symbolic"]
    parts = generic != name["concrete"] or _PLACEHOLDER.search(generic) is not None
    return generic if parts else f"<{generic}>"


# What follows the checked value of a ``bits`` check: ``& 0x00FF == 0x00F3``, or
# ``== 0x00F3`` without a mask.
_BITS = re.compile(r"(?:& (?P<mask>\S+) )?== (?P<expected>.+)\Z", re.DOTALL)


def _bits_rest(
    d: Mapping[str, Any],
    rest: str,
    loc: Optional[Location],
    hint: Optional[str],
    spelled: Optional[str],
) -> tuple[Optional[dict[str, str]], Optional[str]]:
    """For a ``bits`` check (``<value> & <mask> == <expected>``): the expected
    value's operand name, if it has one, and the text after the checked value for
    the procedure, or None when ``rest`` holds no value of this run.

    The expected value reads as its operand name (``== <wdata>``), else as its
    source when that is not a literal; the mask as its source when that is not a
    literal (``& <mask> ==``). A literal stays as pytest-verifier wrote it when
    the call fixes the format, else as the test wrote it (:func:`_expected`).
    pytest-verifier shows the expected value under the mask (``0x00F3`` for
    ``0xABF3`` under ``0x00FF``), which depends on the mask: beside a mask given
    by name, a literal expected value is shown whole (``rendered["expected"]``)."""
    m = _BITS.match(rest)
    if m is None:
        return None, None
    name = _operand(d, "expected_name")
    syms = None
    if loc is not None:
        syms = _expected(loc.file, loc.line, "bits", hint, spelled, _format(d) or (None, None))
    expected_sym, mask_sym = syms or (None, None)
    mask = m["mask"]
    generic_mask = mask if mask is None or mask_sym is None else mask_sym
    expected = _generic(name) if name is not None else expected_sym
    if expected is None:
        if generic_mask == mask:
            return name, None  # literals only, in a format the call fixes
        rendered = d.get("rendered")
        whole = _line(rendered.get("expected")) if isinstance(rendered, Mapping) else None
        expected = whole or m["expected"]
    head = "" if generic_mask is None else f"& {generic_mask} "
    return name, f"{head}== {expected}"


def _format(d: Mapping[str, Any]) -> Optional[tuple[Optional[str], Optional[int]]]:
    """The format a check shows its values in, ``(radix, width)`` as the record has
    them; None for a check without one (it reads as it always did)."""
    radix, width = d.get("radix"), d.get("width")
    if not isinstance(radix, str) and not isinstance(d.get("rendered"), Mapping):
        return None
    return (radix if isinstance(radix, str) else None, width if _is_int(width) else None)


def _tolerance(d: Mapping[str, Any], rest: str, head: str) -> Optional[str]:
    """The tolerances of an ``approx`` with a named expected value (`` ± 0.05V``,
    `` ± 0.5V (abs) ± 1% (rel)``): what follows the value in parentheses after
    the name; when the description shows the name another way, its last parts,
    one per tolerance of the record. None when neither is there."""
    if rest.startswith(head):
        end = rest.find(") ±", len(head))  # the value is a number: the first one closes it
        if end >= 0:
            return rest[end + 1:]
    count = sum(d.get(k) is not None for k in ("abs_tol", "rel_tol"))
    parts = rest.rsplit(" ± ", count) if count else []
    return "".join(f" ± {p}" for p in parts[1:]) if len(parts) == count + 1 else None


def _word(
    check: Check, prefix: str, symbolic: str, concrete: str, rest: str, sym_rest: Optional[str]
) -> None:
    """``[prefix]<checked value> <rest>``: the report's text and the procedure's."""
    check.text = f"{prefix}{concrete} {rest}"
    sym = f"{prefix}{symbolic} {sym_rest or rest}"
    check.symbolic_text = None if sym == check.text else sym


def _rest(description: str, name: Any) -> Optional[str]:
    """What follows ``Verify '<name>' `` in a description (``== 1``, ``is True``),
    or None when the description has another shape."""
    prefix = f"Verify '{name}' "
    if description.startswith(prefix):
        return description[len(prefix):] or None
    m = _DESCRIPTION.match(description)
    return m.group(1) if m else None


_DESCRIPTION = re.compile(r"Verify '.*?' ((?:==|!=|>=|<=|>|<|∈|is|&) .*)\Z", re.DOTALL)


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
    loose: int = 0
    """How loosely the expression binds: 2 when ``<it> == x`` would read as
    another expression (a comparison, ``and``/``or``/``not``, a conditional, a
    lambda, ``:=``), 1 when only ``<it> & x`` would (``|``, ``^``), else 0."""


def _loose(node: ast.expr) -> int:
    if isinstance(node, (ast.Compare, ast.BoolOp, ast.IfExp, ast.Lambda, ast.NamedExpr)):
        return 2
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return 2
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.BitOr, ast.BitXor)):
        return 1
    return 0


def _grouped(subject: _Subject, masked: bool) -> _Subject:
    """The checked value of a ``bits`` check in parentheses when the line would
    otherwise state another expression: ``(a | 1) & 0x00FF == 0x00F3``."""
    if subject.loose < (1 if masked else 2):
        return subject
    return replace(subject, symbolic=f"({subject.symbolic})", concrete=f"({subject.concrete})")


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
def _call(
    path: str, line: int, method: str, hint: Optional[str], spelled: Optional[str] = None
) -> Optional[ast.Call]:
    """The ``.method(...)`` call that made a check at ``path:line``. With several
    on the line, the one whose checked value reads ``hint``, or whose
    ``actual_name=`` spells ``spelled``; None when that does not tell them apart
    (the check then keeps its plain text)."""
    calls = calls_at(path, line, method)
    if len(calls) == 1:
        return calls[0]
    if hint is not None:
        matches = [c for c in calls if _reads_as(path, _first(c), hint)]
    elif spelled is not None:
        matches = [c for c in calls if _spells(c, spelled)]
    else:
        return None
    return matches[0] if len(matches) == 1 else None


def _spells(call: ast.Call, name: str) -> bool:
    """Whether the call's ``actual_name=`` can have given ``name``: a string that
    is it, or an f-string (a t-string) whose literal parts are in it in order,
    the first at its start and the last at its end."""
    node = next((kw.value for kw in call.keywords if kw.arg == "actual_name"), None)
    if isinstance(node, ast.Constant):
        return _line(node.value) == name
    if type(node).__name__ not in ("JoinedStr", "TemplateStr"):  # TemplateStr: Python 3.14+
        return False
    literal = [
        _UNSAFE.sub(" ", v.value) if isinstance(v, ast.Constant) and isinstance(v.value, str) else None
        for v in getattr(node, "values", ())
    ]
    if literal and literal[0] is not None:
        literal[0] = literal[0].lstrip()
    if literal and literal[-1] is not None:
        literal[-1] = literal[-1].rstrip()
    pattern = "".join(".*" if text is None else re.escape(text) for text in literal)
    return re.fullmatch(pattern, name, re.DOTALL) is not None


def _reads_as(path: str, node: Optional[ast.expr], hint: str) -> bool:
    if node is None or isinstance(node, ast.Starred):
        return False
    texts = {" ".join(ast.unparse(node).split()), source_text(path, node)}
    if hint in texts:
        return True
    return hint.endswith("...") and any(t and t.startswith(hint[:-3]) for t in texts)


@lru_cache(maxsize=4096)
def _subject(
    path: str, line: int, method: str, hint: Optional[str] = None, spelled: Optional[str] = None
) -> Optional[_Subject]:
    """The first argument of the ``.method(...)`` call at ``path:line``; None when
    it is a literal, the call is ambiguous or the source is not available. Never
    raises."""
    try:
        call = _call(path, line, method, hint, spelled)
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
            loose=_loose(node),
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
    named: Optional[tuple[str, str]] = None
    """The checked value's operand name (symbolic, concrete), from the check's
    ``actual_name=``: it words the check whatever the source or a read says."""

    def apply(self, check: Check, symbolic: str, concrete: str) -> None:
        if self.named is not None:
            symbolic, concrete = self.named
        _word(check, self.prefix, symbolic, concrete, self.rest, self.sym_rest)


def _symbolic_rest(
    d: Mapping[str, Any],
    rest: str,
    loc: Optional[Location],
    hint: Optional[str] = None,
    spelled: Optional[str] = None,
) -> Optional[str]:
    """The text after the name for the procedure when the expected value is a name
    or an expression (``== <age>``); None when it is a literal (``rest`` already
    reads right). In a check that shows its values in a format, a literal reads
    as the test wrote it unless the call fixes the format (:func:`_expected`)."""
    kind = str(d.get("check_type"))
    if loc is None or (kind != "between" and kind not in _COMPARISONS):
        return None
    syms = _expected(loc.file, loc.line, kind, hint, spelled, _format(d))
    if syms is None:
        return None
    units = str(d["units"]) if d.get("units") else ""
    if kind == "between":
        lo, hi = syms
        bounds = rest[rest.index("∈") + 1:].strip() if "∈" in rest else ""
        concrete = [b.strip() for b in bounds[1:-1].split(",")] if bounds[:1] in "[(" else []
        if len(concrete) != 2:
            return None
        lo_text = _with_units(lo, units) if lo else concrete[0]
        hi_text = _with_units(hi, units) if hi else concrete[1]
        return f"∈ {bounds[0]}{lo_text}, {hi_text}{bounds[-1]}"
    op = _COMPARISONS[kind][0]
    tolerance = rest[rest.index(" ±"):] if kind == "approx" and " ±" in rest else ""
    assert syms[0] is not None
    return f"{op} {_with_units(syms[0], units)}{tolerance}"


def _with_units(text: str, units: str) -> str:
    """A value of the procedure with its units: after a space when it is given by
    name (``<limit> LSB``), right after a literal, as pytest-verifier writes a
    number (``0x100LSB``)."""
    if not units:
        return text
    return f"{text} {units}" if text.startswith("<") else f"{text}{units}"


def _spread(call: ast.Call) -> bool:
    """Whether the call takes arguments from ``*args`` or ``**kwargs``: what an
    argument that is not written in it may then be is not in the source."""
    return any(kw.arg is None for kw in call.keywords) or any(
        isinstance(a, ast.Starred) for a in call.args
    )


def _passed(call: ast.Call, position: Optional[int], keyword: str) -> Optional[ast.expr]:
    """The argument written in the call for ``keyword`` (or at ``position``, when
    no ``*args`` comes before it); None when it is not written there."""
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    if position is None or len(call.args) <= position:
        return None
    if any(isinstance(a, ast.Starred) for a in call.args[: position + 1]):
        return None
    return call.args[position]


def _literal(node: Optional[ast.expr]) -> Any:
    """The value of a literal argument; ``...`` for an argument that is not one,
    None for one that is not written (or is ``None``)."""
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return ...


def _fixed(call: ast.Call, kind: str, radix: Optional[str], width: Optional[int]) -> bool:
    """Whether the call alone says how the check shows its integers, so that every
    run of it shows a literal the same way: ``radix=`` and ``width=`` are literals
    where written, and account for the record's format. A width the call does not
    give, or a radix that is not the one the call implies (hex, for ``bits`` and
    beside a ``width=``), came from the value (``__verify_format__``): another
    run's value may say otherwise."""
    if _spread(call):
        return False
    given_radix = _literal(_passed(call, None, "radix"))
    given_width = _literal(_passed(call, None, "width"))
    if given_radix is ... or given_width is ...:
        return False
    if given_width is None and width is not None:
        return False
    if given_radix is None:
        return radix is None or (radix == "hex" and (kind == "bits" or given_width is not None))
    return True


@lru_cache(maxsize=4096)
def _expected(
    path: str,
    line: int,
    kind: str,
    hint: Optional[str] = None,
    spelled: Optional[str] = None,
    shown: Optional[tuple[Optional[str], Optional[int]]] = None,
) -> Optional[tuple[Optional[str], ...]]:
    """The expected value's ``<source>`` at a call site (``(low, high)`` for
    ``between``, ``(expected, mask)`` for ``bits``), once per line; None when
    every one is a literal.

    ``shown`` is the format of a check that shows its values in one (radix and
    width, from the record). pytest-verifier's text of a literal then depends on
    that format, ``0x00F3`` at 16 bits and ``0x0F3`` at 12, so it stays only when
    the call fixes the format (:func:`_fixed`). Otherwise an integer literal reads
    as the test wrote it (``0xF3``), and a value passed through ``**kwargs`` or
    ``*args`` as ``<its parameter>`` (``<mask>``): no value of the run is in the
    procedure, whatever the value checked says of its format."""
    call = _call(path, line, _METHODS.get(kind, kind), hint, spelled)
    if call is None:
        return None
    wanted: tuple[tuple[Optional[int], str], ...]
    if kind == "between":
        wanted = ((1, "low"), (2, "high"))
    elif kind == "bits":
        wanted = ((1, "expected"), (None, "mask"))
    else:
        wanted = (_COMPARISONS[kind][1:],)
    if shown is None:
        nodes = [argument(call, 99 if at is None else at, kw) for at, kw in wanted]
        found = tuple(symbolic(path, node) if node is not None else None for node in nodes)
        return None if all(sym is None for sym in found) else found
    fixed = _fixed(call, kind, *shown)
    spread = _spread(call)
    texts: list[Optional[str]] = []
    for at, keyword in wanted:
        node = _passed(call, at, keyword)
        if node is None:
            texts.append(f"<{keyword}>" if spread else None)
            continue
        sym = symbolic(path, node)
        if sym is None and not fixed and _is_int(_literal(node)):
            sym = source_text(path, node)
        texts.append(sym)
    return None if all(text is None for text in texts) else tuple(texts)


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
