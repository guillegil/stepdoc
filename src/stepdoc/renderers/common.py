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
    remark: Optional[str] = None
    """A step's or repeat's ``note=``, shown under its line in both documents."""
    aside: Optional[str] = None
    """A check's note, prose shown after ``code`` in parentheses (CHK-9)."""


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


def action_line(a: Record, symbolic: bool, depth: int, passes: Optional[int] = None) -> Line:
    """One action. ``passes``: how often the loop holding it ran, when it is one
    pass of a collapsed loop (the report then says when its values do not add up
    to one per pass)."""
    meta = a.get("meta") or {}
    target = (a["target"]["symbolic"] or a["target"]["concrete"]) if symbolic else a["target"]["concrete"]
    direction = a.get("direction", "in")
    value = ""
    note = None
    values = a.get("values")
    if direction != "out":
        if symbolic:
            if a["value"]["symbolic"] is not None:
                value = a["value"]["symbolic"]
            elif values:
                value = sequence(values, a.get("values_count"))
                note = "values from this run"
            elif a["value"]["concrete"] is not None:
                value = show(a["value"]["concrete"])
                note = "value from this run"
        elif values:
            value = sequence(values, a.get("values_count"))
            value = _with_unit(value, meta) if direction == "in" else value
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
    if count > 1 and direction != "in" and not values:  # ACT-4: a polling loop, one line
        if not symbolic:
            noun = "reads" if direction == "out" else "calls"
            result = _times(result, count, noun, a)
        code = f"{target}  {value}".rstrip() if op in _BARE_OPS else f"{target} {value}".rstrip()
        return Line(a.get("number"), "Poll", code, result, note=note, depth=depth)
    if direction == "in" and op in _ASSIGN_OPS:
        verb, code = "", f"{target} = {value}" if value else target
    elif op in _BARE_OPS:
        verb, code = "", f"{target}  {value}".rstrip()
    else:
        verb, code = op.capitalize(), f"{target} {value}".rstrip()
    if count > 1:  # the same write again, from one line (a loop)
        verb = f"Repeat {verb.lower()}".rstrip()
        if not symbolic:
            total = a.get("values_count") if values else None
            if total is not None and total != count:  # values of every pass of a loop
                result = _also(result, f"{count} times per pass, {total} values")
            else:
                result = _times(result, count, "times", a)
    elif values and not symbolic and passes is not None and a.get("values_count") != passes:
        # more values than passes: the unfinished last pass of a loop, for one
        result = _also(result, f"{a.get('values_count')} values")
    return Line(a.get("number"), verb, code, result, note=note, depth=depth)


def sequence(values: list[Any], total: Optional[int] = None) -> str:
    """The values a loop sent, compact: all of them up to four (``0, 1, 2``), else
    the first two and the last (``0, 1, … 15``)."""
    cut = total is not None and total > len(values)
    if len(values) < 3 or (len(values) <= 4 and not cut):
        return ", ".join(show(v) for v in values) + (", …" if cut else "")
    return f"{show(values[0])}, {show(values[1])}, … {show(values[-1])}"


def _also(result: Optional[str], words: str) -> str:
    return f"{result} ({words})" if result else f"({words})"


def _times(result: Optional[str], count: int, noun: str, e: Record) -> str:
    spent = ""
    if e.get("t_last") is not None and e.get("t") is not None:
        spent = f", {(e['t_last'] - e['t']) * 1000:.0f} ms"
    return f"{result} ({count} {noun}{spent})" if result else f"({count} {noun}{spent})"


def loop_words(r: Record) -> str:
    """``repeat until FIFO is empty, counting write_count`` (ACT-10)."""
    words = f"repeat until {r['until']}" if r.get("until") else "repeat"
    if r.get("counter"):
        inc = r.get("inc")
        if inc is None or inc == 1:
            words += f", counting {r['counter']}"
        else:
            words += f", {r['counter']} {'+' if inc >= 0 else '-'}{show(abs(inc))} each pass"
    return words


def times(count: Any) -> Optional[str]:
    """How often a loop ran, for the report: ``once``, ``16 times``."""
    if not isinstance(count, int) or isinstance(count, bool):
        return None
    return "once" if count == 1 else f"{count} times"


def repeat_line(r: Record, symbolic: bool, depth: int) -> Line:
    """ACT-10: an untitled ``repeat()`` block or a loop that collapsed on its own,
    ``Repeat until FIFO is empty``; the report adds how often."""
    words = loop_words(r)
    verb = f"{r['title']} ({words})" if r.get("title") else words[0].upper() + words[1:]
    note = None
    count = r.get("count")
    if not symbolic and count is not None:
        note = times(count)
        if count > 1 and r.get("t_last") is not None and r.get("t") is not None:
            note = f"{note}, {(r['t_last'] - r['t']) * 1000:.0f} ms"
    return Line(r.get("number"), verb, "", note=note, depth=depth, remark=r.get("note"))


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
    code = (c.get("symbolic_text") or c["text"]) if symbolic else c["text"]
    if c.get("selected"):
        code += f" [→ {c['selected']}]"
    note = c.get("note") or None
    return Line(c.get("number"), "Verify", code, result, mark=mark, depth=depth, aside=note)


def entry_lines(
    entries: list[Record], symbolic: bool, depth: int, passes: Optional[int] = None
) -> Iterator[Line]:
    """Lines of ``entries``; ``passes`` when they are one pass of a loop that ran
    that often."""
    for e in entries:
        kind = e["type"]
        if kind == "step":
            yield from step_lines(e, symbolic, depth)
        elif kind == "repeat":
            yield repeat_line(e, symbolic, depth)
            yield from entry_lines(e["entries"], symbolic, depth + 1, _passes(e.get("count")))
        elif kind == "check":
            yield check_line(e, symbolic, depth)
            for child in e.get("children") or []:
                yield check_line(child, symbolic, depth + 1)
        else:
            yield action_line(e, symbolic, depth, passes)


def step_lines(s: Record, symbolic: bool, depth: int = 0) -> Iterator[Line]:
    mark = None
    note = None
    if not symbolic:
        mark = STATUS_MARK.get(s["status"], "?")
        ms = (s["ended"] - s["started"]) * 1000
        note = f"{ms:.0f} ms" if ms >= 1 else None
        loop = s.get("loop")
        if loop and times(loop.get("count")):
            note = " · ".join(p for p in (times(loop["count"]), note) if p)
        if s.get("error"):
            note = (note + " · " if note else "") + s["error"].splitlines()[0]
    title = f"{s['title']} ({loop_words(s['loop'])})" if s.get("loop") else s["title"]
    yield Line(
        s["number"], "", title, mark=mark, note=note, depth=depth, is_step=True, remark=s.get("note")
    )
    loop = s.get("loop")
    yield from entry_lines(s["entries"], symbolic, depth + 1, _passes(loop and loop.get("count")))


def _passes(count: Any) -> Optional[int]:
    return count if isinstance(count, int) and not isinstance(count, bool) else None


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
    if line.aside:
        body += f" ({' '.join(line.aside.split())})"
    if line.result:
        body += f" -> {line.result}"
    return "   " * line.depth + head + body


def remark_lines(line: Line) -> list[str]:
    """A line's ``note=`` as plain text, indented under it like its entries."""
    if not line.remark:
        return []
    pad = "   " * (line.depth + 1)
    first, *rest = line.remark.splitlines()
    return [f"{pad}Note: {first}", *(f"{pad}      {r}" for r in rest)]


def procedure_lines(test: Record, sections: tuple[str, ...] = SECTIONS) -> list[str]:
    out = []
    for line in test_lines(test, symbolic=True, sections=sections):
        out.append(plain(line))
        out.extend(remark_lines(line))
    return out


def report_lines(test: Record) -> list[str]:
    out = []
    for line in test_lines(test, symbolic=False):
        text = plain(line)
        tail = " ".join(p for p in (line.mark, line.note) if p)
        out.append(f"{text}   {tail}" if tail else text)
        out.extend(remark_lines(line))
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


    @property
    def param_table(self) -> list[tuple[str, Optional[str], list[str]]]:
        """``(name, description, values)`` per parameter. An adapter's domain
        (``integer 0 … 4095``) replaces the list of sampled values (DRY-4)."""
        info: dict[str, Record] = {}
        for case in self.cases:
            for name, item in (case.get("param_info") or {}).items():
                info.setdefault(name, item)
        rows = []
        for name, values in self.params.items():
            item = info.get(name) or {}
            domain = item.get("domain")
            rows.append((name, item.get("description"), [domain] if domain else values))
        return rows

    @property
    def strategy(self) -> Optional[str]:
        """The generator of the cases' values, when an adapter names it."""
        names = {(c.get("vector") or {}).get("strategy") for c in self.cases}
        names.discard(None)
        return names.pop() if len(names) == 1 else None


def vector_note(test: Record) -> Optional[str]:
    """``directed: youngest``, ``random``: which generated row a case runs."""
    vector = test.get("vector") or {}
    kind, name = vector.get("kind"), vector.get("name")
    if not kind:
        return None
    return f"{kind}: {name}" if name else str(kind)


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
