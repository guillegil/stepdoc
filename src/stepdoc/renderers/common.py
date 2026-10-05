"""Shared logic for renderers. Everything here reads the JSON run record (D6),
never the in-memory recorder, so ``stepdoc render`` can reuse it later."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator, Optional

Record = dict[str, Any]

STATUS_MARK = {"passed": "✓", "failed": "✗", "error": "✗", "unknown": "?", "not_run": "–"}
OUTCOME_TEXT = {"passed": "PASSED", "failed": "FAILED", "error": "ERROR", "skipped": "SKIPPED", None: "?"}

# Ops whose "in" form reads as an assignment. Everything else is "<Op> <target> <value>".
_ASSIGN_OPS = {"write", "set"}
# Ops whose target already says what happens ("POST /users").
_BARE_OPS = {"request"}


@dataclass
class Line:
    """One rendered entry: ``verb`` is prose, ``code`` and ``result`` are literal text."""

    number: Optional[str]
    verb: str
    code: str
    result: Optional[str] = None
    mark: Optional[str] = None
    note: Optional[str] = None
    depth: int = 0
    is_step: bool = False


def show(value: Any) -> str:
    """A concrete JSON value as text (tagged objects back to something readable)."""
    if isinstance(value, dict) and "$type" in value:
        if "$enum" in value:
            return str(value["$enum"])
        if "$base64" in value:
            return f"<{len(value['$base64']) * 3 // 4} bytes>"
        return str(value.get("$repr", value["$type"]))
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k!r}: {show(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(show(v) for v in value) + "]"
    return repr(value)


def _with_unit(text: str, meta: dict[str, Any]) -> str:
    unit = meta.get("unit")
    return f"{text} {unit}" if unit else text


def action_line(a: Record, symbolic: bool, depth: int) -> Line:
    meta = a.get("meta") or {}
    target = (a["target"]["symbolic"] or a["target"]["concrete"]) if symbolic else a["target"]["concrete"]
    direction = a.get("direction", "in")
    value = ""
    note = None
    if direction != "out":
        if symbolic:
            if a["value"]["symbolic"] is not None:
                value = a["value"]["symbolic"]
            elif a["value"]["concrete"] is not None:
                value = show(a["value"]["concrete"])
                note = "value from this run"
        elif a["value"]["concrete"] is not None:
            value = _with_unit(show(a["value"]["concrete"]), meta) if direction == "in" else show(a["value"]["concrete"])
    result = None
    if direction == "out":
        if symbolic:
            result = f"<{a['bound_to']}>" if a.get("bound_to") else None
        else:
            result = _with_unit(show(a["value"]["concrete"]), meta)
    elif direction == "exchange" and not symbolic:
        if "result" in meta:
            result = show(meta["result"])
        elif "status" in meta:
            result = show(meta["status"])

    op = a["op"]
    count = a.get("count", 1)
    if count > 1:  # ACT-4: a polling loop, one line
        if not symbolic:
            noun = "reads" if direction == "out" else "calls"
            spent = ""
            if a.get("t_last") is not None and a.get("t") is not None:
                spent = f", {(a['t_last'] - a['t']) * 1000:.0f} ms"
            result = f"{result} ({count} {noun}{spent})" if result else f"({count} {noun}{spent})"
        code = f"{target}  {value}".rstrip() if op in _BARE_OPS else f"{target} {value}".rstrip()
        return Line(a.get("number"), "Poll", code, result, note=note, depth=depth)
    if direction == "in" and op in _ASSIGN_OPS:
        verb, code = "", f"{target} = {value}" if value else target
    elif op in _BARE_OPS:
        verb, code = "", f"{target}  {value}".rstrip()
    else:
        verb, code = op.capitalize(), f"{target} {value}".rstrip()
    return Line(a.get("number"), verb, code, result, note=note, depth=depth)


def check_line(c: Record, symbolic: bool, depth: int) -> Line:
    mark = None
    result = None
    if not symbolic:
        mark = {True: "✓", False: "✗", None: "·"}[c["passed"]]
        if c.get("actual") is not None:
            result = f"got {show(c['actual'])}"
        elif c.get("reads"):  # ACT-5: the reads that fed the check
            reads = c["reads"]
            if len(reads) == 1:
                result = f"got {show(reads[0]['value']['concrete'])}"
            else:
                result = "got " + ", ".join(
                    f"{r['target']['concrete']} = {show(r['value']['concrete'])}" for r in reads
                )
    code = c["text"]
    if c.get("selected"):
        code += f" [→ {c['selected']}]"
    return Line(c.get("number"), "Verify", code, result, mark=mark, depth=depth)


def entry_lines(entries: list[Record], symbolic: bool, depth: int) -> Iterator[Line]:
    for e in entries:
        kind = e["type"]
        if kind == "step":
            yield from step_lines(e, symbolic, depth)
        elif kind == "check":
            yield check_line(e, symbolic, depth)
            for child in e.get("children") or []:
                yield check_line(child, symbolic, depth + 1)
        else:
            yield action_line(e, symbolic, depth)


def step_lines(s: Record, symbolic: bool, depth: int = 0) -> Iterator[Line]:
    mark = None
    note = None
    if not symbolic:
        mark = STATUS_MARK.get(s["status"], "?")
        ms = (s["ended"] - s["started"]) * 1000
        note = f"{ms:.0f} ms" if ms >= 1 else None
        if s.get("error"):
            note = (note + " · " if note else "") + s["error"].splitlines()[0]
    yield Line(s["number"], "", s["title"], mark=mark, note=note, depth=depth, is_step=True)
    yield from entry_lines(s["entries"], symbolic, depth + 1)


SECTIONS = ("setup", "procedure", "teardown")
SECTION_TITLE = {"setup": "Setup", "procedure": "Procedure", "teardown": "Teardown"}


def has_section(test: Record, section: str) -> bool:
    return any(e.get("section", "procedure") == section for e in test["steps"] + test["unscoped"])


def test_lines(test: Record, symbolic: bool, sections: tuple[str, ...] = SECTIONS) -> list[Line]:
    """Lines of one test. Setup and teardown (STEP-8) get a heading; the procedure
    gets one only when the test has a setup or teardown section to tell it apart."""
    lines: list[Line] = []
    framed = any(has_section(test, s) for s in ("setup", "teardown"))
    for section in sections:
        steps = [s for s in test["steps"] if s.get("section", "procedure") == section]
        loose = [e for e in test["unscoped"] if e.get("section", "procedure") == section]
        if not steps and not loose:
            continue
        if section != "procedure":
            lines.append(Line(None, "", SECTION_TITLE[section], is_step=True))
            lines.extend(entry_lines(loose, symbolic, 1))
        else:
            if framed and len(sections) > 1:
                lines.append(Line(None, "", SECTION_TITLE[section], is_step=True))
            if loose:
                lines.append(Line(None, "", "Unscoped", is_step=True))
                lines.extend(entry_lines(loose, symbolic, 1))
        for s in steps:
            lines.extend(step_lines(s, symbolic))
    return lines


def plain(line: Line) -> str:
    """A line as plain text, without status marks or notes."""
    head = f"{line.number}. " if line.number else ("- " if not line.is_step else "")
    body = " ".join(p for p in (line.verb, line.code) if p)
    if line.result:
        body += f" -> {line.result}"
    return "   " * line.depth + head + body


def procedure_lines(test: Record, sections: tuple[str, ...] = SECTIONS) -> list[str]:
    return [plain(line) for line in test_lines(test, symbolic=True, sections=sections)]


def report_lines(test: Record) -> list[str]:
    out = []
    for line in test_lines(test, symbolic=False):
        text = plain(line)
        tail = " ".join(p for p in (line.mark, line.note) if p)
        out.append(f"{text}   {tail}" if tail else text)
    return out


# --------------------------------------------------------------------------- #
# Grouping cases into procedures (PAR-2)
# --------------------------------------------------------------------------- #


@dataclass
class Procedure:
    procedure_id: str
    cases: list[Record]
    variants: list[tuple[list[str], list[Record]]]
    """Distinct procedures among the cases (procedure section only), with the cases
    that follow each. More than one variant means the code took different paths
    for different values."""
    setup: Optional[Record] = None
    """The case with the fullest setup section: session-scoped fixtures only set up
    in the first test that uses them."""
    teardown: Optional[Record] = None

    @property
    def params(self) -> dict[str, list[str]]:
        table: dict[str, list[str]] = {}
        for case in self.cases:
            for name, value in case["params"].items():
                shown = show(value)
                values = table.setdefault(name, [])
                if shown not in values:
                    values.append(shown)
        return table


def procedures(record: Record) -> list[Procedure]:
    groups: dict[str, list[Record]] = {}
    for t in record["tests"]:
        groups.setdefault(t["procedure_id"], []).append(t)
    result = []
    for pid, cases in groups.items():
        variants: list[tuple[list[str], list[Record]]] = []
        for case in cases:
            lines = procedure_lines(case, ("procedure",))
            for i, (known, members) in enumerate(variants):
                # A case that stopped early (a failure) follows the longer procedure.
                if known[: len(lines)] == lines:
                    members.append(case)
                    break
                if lines[: len(known)] == known:
                    variants[i] = (lines, members + [case])
                    break
            else:
                variants.append((lines, [case]))
        result.append(
            Procedure(pid, cases, variants, _fullest(cases, "setup"), _fullest(cases, "teardown"))
        )
    return result


def _fullest(cases: list[Record], section: str) -> Optional[Record]:
    best = max(cases, key=lambda c: len(procedure_lines(c, (section,))))
    return best if has_section(best, section) else None


def anchors(ids: list[str]) -> list[str]:
    """Stable, unique HTML ids for test or procedure ids (links from contents)."""
    seen: dict[str, int] = {}
    out = []
    for i in ids:
        base = re.sub(r"[^A-Za-z0-9_-]+", "-", i).strip("-").lower() or "test"
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.append(base if n == 0 else f"{base}-{n + 1}")
    return out


def short_name(procedure_id: str) -> str:
    return procedure_id.rsplit("::", 1)[-1]
