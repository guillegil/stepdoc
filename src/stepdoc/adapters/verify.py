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

import os
from typing import Any, Mapping, Optional, Sequence

import pytest

from ..core.model import Check, Location, Section, Step
from ..core.recorder import Recorder
from . import Adapter, Host
from ._source import argument, call_at, same_file, statement_span, symbolic

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


def _location(text: Any, root: str) -> Optional[Location]:
    """``"tests/test_x.py:12"`` (relative to the rootdir) as a Location."""
    if not isinstance(text, str) or ":" not in text:
        return None
    path, _, line = text.rpartition(":")
    if not line.isdigit():
        return None
    return Location(os.path.join(root, path), int(line))


def to_check(d: Mapping[str, Any], root: str) -> Check:
    """A verify check descriptor as a stepdoc Check."""
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
    return Check(
        text=text,
        passed=passed if isinstance(passed, bool) else None,
        expected=d.get("expected"),
        actual=d.get("actual"),
        detail=None if passed is True else (d.get("error") or d.get("detail")),
        children=[to_check(c, root) for c in d.get("child_checks") or [] if isinstance(c, Mapping)],
        selected=None if selected is None else str(selected),
        location=location,
        source=d,
        symbolic_text=_symbolic_text(d, text, location),
    )


def _symbolic_text(d: Mapping[str, Any], text: str, loc: Optional[Location]) -> Optional[str]:
    """The procedure's text when the expected value is a name or an expression;
    None when it is a literal (``text`` already reads right)."""
    kind = str(d.get("check_type"))
    if loc is None:
        return None
    call = call_at(loc.file, loc.line, kind)
    if call is None:
        return None
    units = f" {d['units']}" if d.get("units") else ""
    name = f"'{d.get('name')}'"
    if kind == "between":
        low, high = argument(call, 1, "low"), argument(call, 2, "high")
        lo = symbolic(loc.file, low) if low is not None else None
        hi = symbolic(loc.file, high) if high is not None else None
        if lo is None and hi is None:
            return None
        bounds = text[text.index("∈") + 1:].strip() if "∈" in text else ""
        concrete = [b.strip() for b in bounds[1:-1].split(",")] if bounds[:1] in "[(" else []
        if len(concrete) != 2:
            return None
        lo_text, hi_text = (lo + units if lo else concrete[0]), (hi + units if hi else concrete[1])
        return f"{name} ∈ {bounds[0]}{lo_text}, {hi_text}{bounds[-1]}"
    if kind not in _COMPARISONS:
        return None
    op, position, keyword = _COMPARISONS[kind]
    node = argument(call, position, keyword)
    sym = symbolic(loc.file, node) if node is not None else None
    if sym is None:
        return None
    tolerance = text[text.index(" ±"):] if kind == "approx" and " ±" in text else ""
    return f"{name} {op} {sym}{units}{tolerance}"


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
        self.cursor = -1
        self.seen: dict[int, set[tuple[str, int]]] = {}
        self.next_index: dict[int, int] = {}

    def _walk(self, steps: Sequence[Step], parent: tuple[Step, ...]) -> None:
        for s in steps:
            path = parent + (s,)
            self.paths.append(path)
            self._walk(s.children, path)

    def _contains(self, s: Step, anchor: Location) -> bool:
        if s.location is None or not same_file(s.location.file, anchor.file):
            return False
        first, last = statement_span(s.location.file, s.location.line)
        return first <= anchor.line <= last

    def _allowed(self, i: int) -> bool:
        if i >= self.cursor or self.cursor < 0:
            return True
        current = self.paths[self.cursor]
        return self.paths[i] == current[: len(self.paths[i])]  # an ancestor of the last match

    def place(self, d: Mapping[str, Any], section: Section) -> None:
        check = to_check(d, self.root)
        anchors = [a for a in (check.location, _location(d.get("called_from"), self.root)) if a]
        best: Optional[int] = None
        for i, path in enumerate(self.paths):
            s = path[-1]
            if s.section != section or not self._allowed(i):
                continue
            if not any(self._contains(s, a) for a in anchors):
                continue
            if best is not None and self.paths[best] != path[: len(self.paths[best])]:
                # Not nested in the match so far: a later run of the same step.
                key = (anchors[0].file, anchors[0].line)
                if key in self.seen.get(best, set()):
                    best = i
                continue
            best = i  # nested deeper (or the first match)
        if best is None:
            check.section = section
            self.rec.insert_check((), check)
            return
        self.cursor = best
        if anchors:
            self.seen.setdefault(best, set()).add((anchors[0].file, anchors[0].line))
        path = self.paths[best]
        index = self._index(best, path[-1], anchors)
        self.rec.insert_check(path, check, index)
        self.next_index[best] = index + 1

    def _index(self, i: int, s: Step, anchors: list[Location]) -> int:
        """Where the check goes among the step's entries: before the first entry,
        from the last insertion on, made on a later line of the same file."""
        start = self.next_index.get(i, 0)
        anchor = next((a for a in anchors if s.location and same_file(a.file, s.location.file)), None)
        if anchor is None:
            return len(s.entries)
        for j in range(start, len(s.entries)):
            loc = s.entries[j].location
            if loc is not None and same_file(loc.file, anchor.file) and loc.line > anchor.line:
                return j
        return len(s.entries)
