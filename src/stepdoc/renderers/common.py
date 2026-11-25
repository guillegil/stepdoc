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
    label: Optional[str] = None
    """A check's name with its quotes (``'FIFO not full':``), words shown between
    ``verb`` and ``code`` (CHK-10)."""
    is_check: bool = False
    branch: Optional[tuple[str, Optional[str], Optional[str]]] = None
    """For a branch of a guarded check (CHK-12): its opening word (``If``,
    ``Else if``, ``Otherwise``), condition (code) and label (words), shown before
    the branch's check: ``If mode == "fast" (fast mode): Verify ...``."""
    not_run: bool = False
    """A branch this run did not check (the report greys it out where it can)."""


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
    if count > 1 and direction != "in":  # ACT-4: a polling loop, one line
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
            result = _times(result, count, "times", a)
    return Line(a.get("number"), verb, code, result, note=note, depth=depth)


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
    each = c.get("each")
    if not symbolic and isinstance(each, dict) and isinstance(each.get("count"), int):
        result = each_result(each, c["passed"] is not None)
    code = (c.get("symbolic_text") or c["text"]) if symbolic else c["text"]
    label, code = _split_label(c.get("label"), code)
    if isinstance(c.get("index"), int):  # CHK-11: one failed item of a folded check
        label = f"item [{c['index']}]:"
    if c.get("branches"):  # CHK-12: the branches follow, each on its own line
        name = c.get("label")
        label = f"'{name}', depending on the condition:" if name else "depending on the condition:"
        code = ""
        result = None
    elif c.get("selected"):
        code += f" [→ {c['selected']}]"
    note = c.get("note") or None
    return Line(
        c.get("number"), "Verify", code, result, mark=mark, depth=depth, aside=note, label=label,
        is_check=True,
    )


def branch_opening(b: Record, index: int) -> tuple[str, Optional[str], Optional[str]]:
    """``("If", 'mode == "fast"', "fast mode")``, ``("Else if", ...)``,
    ``("Otherwise", None, None)``. A branch whose condition is not known is named
    by its label, or by its position."""
    if b.get("default"):
        return "Otherwise", None, None
    word = "If" if index == 0 else "Else if"
    cond, label = b.get("condition"), b.get("label")
    if not cond and not label:
        label = f"branch {index + 1}"
    return word, cond or None, label or None


def branch_check(b: Record, symbolic: bool) -> Optional[Record]:
    """The check a branch line shows: in the report the check this run built;
    in the procedure, and for a branch this run did not build, the check as its
    source words it (the same in every case), else the built one."""
    check: Optional[Record] = b.get("check")
    if not symbolic and check is not None:
        return check
    wording: Optional[Record] = b.get("wording")
    return wording or check


def branch_lines(c: Record, symbolic: bool, depth: int) -> Iterator[Line]:
    """CHK-12: one line per branch of a guarded check. In the report the branch
    the run took has its result and mark; the others say why they were not
    checked (``condition false`` before it, ``not evaluated`` after it)."""
    seen_taken = False
    for i, b in enumerate(c.get("branches") or []):
        check = branch_check(b, symbolic)
        taken = bool(b.get("taken"))
        if check is not None:
            line = check_line(check, symbolic or not taken, depth)
        else:
            line = Line(None, "Verify", b.get("source") or "", depth=depth, is_check=True)
        line.number = None
        line.branch = branch_opening(b, i)
        if not symbolic:
            if taken:
                seen_taken = True
            else:
                line.mark = line.result = None
                line.not_run = True
                line.note = "condition false" if b.get("holds") is False and not seen_taken else "not evaluated"
        else:
            line.mark = line.result = None
        yield line


def branch_text(line: Line) -> str:
    """A branch line's opening as plain text: ``If mode == "fast" (fast mode):``."""
    if line.branch is None:
        return ""
    word, cond, label = line.branch
    parts = [word] + ([cond] if cond else []) + ([f"({label})" if cond else label] if label else [])
    return " ".join(parts) + ":"


def each_result(each: Record, judged: bool = True) -> str:
    """``12 of 12 passed`` or ``2 of 12 failed`` for a folded check (CHK-11);
    ``12 items`` when it was not judged (a dry run)."""
    count, failed = each["count"], each.get("failed") or 0
    if not judged:
        return f"{count} item{'' if count == 1 else 's'}"
    return f"{failed} of {count} failed" if failed else f"{count} of {count} passed"


def _split_label(label: Any, text: str) -> tuple[Optional[str], str]:
    """A check's text as its name in quotes and the code after it: ``'Idle':`` and
    ``dev.ctl.tx == 0``, or ``'HTTP status'`` and ``== 201``. Joined by a space
    they are the text again."""
    if isinstance(label, str) and label:
        for head in (f"'{label}':", f"'{label}'"):
            if text.startswith(head + " ") and len(text) > len(head) + 1:
                return head, text[len(head) + 1:]
    return None, text


def entry_lines(entries: list[Record], symbolic: bool, depth: int) -> Iterator[Line]:
    for e in entries:
        kind = e["type"]
        if kind == "step":
            yield from step_lines(e, symbolic, depth)
        elif kind == "repeat":
            yield repeat_line(e, symbolic, depth)
            yield from entry_lines(e["entries"], symbolic, depth + 1)
        elif kind == "check":
            yield check_line(e, symbolic, depth)
            if symbolic and e.get("each"):
                continue  # CHK-11: the failed items belong to this run, not the procedure
            for child in e.get("children") or []:
                yield check_line(child, symbolic, depth + 1)
            yield from branch_lines(e, symbolic, depth + 1)
        else:
            yield action_line(e, symbolic, depth)


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
    if s.get("applied_in"):
        # PRE-3: a shared fixture applied it for an earlier test; both documents say where.
        if s["status"] in ("failed", "error"):
            note = f"not established, failed in {s['applied_in']}"
        else:
            note = f"already established, applied in {s['applied_in']}"
        if not symbolic and s.get("error"):
            note += " · " + s["error"].splitlines()[0]
    title = f"{s['title']} ({loop_words(s['loop'])})" if s.get("loop") else s["title"]
    yield Line(
        s["number"], "", title, mark=mark, note=note, depth=depth, is_step=True, remark=s.get("note")
    )
    yield from entry_lines(s["entries"], symbolic, depth + 1)


SECTIONS = ("precondition", "setup", "procedure", "teardown")
SECTION_TITLE = {
    "precondition": "Pre-conditions",
    "setup": "Setup",
    "procedure": "Procedure",
    "teardown": "Teardown",
}


def has_section(test: Record, section: str) -> bool:
    return any(e.get("section", "procedure") == section for e in test["steps"] + test["unscoped"])


def test_lines(test: Record, symbolic: bool, sections: tuple[str, ...] = SECTIONS) -> list[Line]:
    """Lines of one test. Pre-conditions (PRE-1), setup and teardown (STEP-8) get a
    heading; the procedure gets one only when the test has another section to
    tell it apart."""
    lines: list[Line] = []
    framed = any(has_section(test, s) for s in SECTIONS if s != "procedure")
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
    return "   " * line.depth + head + line_body(line)


def line_body(line: Line) -> str:
    """A line's text without its number, indent, status mark or notes."""
    body = " ".join(p for p in (branch_text(line), line.verb, line.label, line.code) if p)
    if line.aside:
        body += f" ({' '.join(line.aside.split())})"
    if line.result:
        body += f" -> {line.result}"
    return body


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
        established = line.is_step and line.note  # PRE-3; other notes are the report's
        out.append(f"{plain(line)}   {line.note}" if established else plain(line))
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
    precondition: Optional[Record] = None
    """The case with the fullest pre-conditions: the one that applied them, when
    the others found them already established."""

    @property
    def framed(self) -> bool:
        """Whether the procedure needs its own heading, to tell it from the other sections."""
        return any(c is not None for c in (self.precondition, self.setup, self.teardown))

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
            lines = path_lines(case)
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
            Procedure(
                pid,
                cases,
                variants,
                _fullest(cases, "setup"),
                _fullest(cases, "teardown"),
                _fullest(cases, "precondition"),
            )
        )
    return result


def path_lines(case: Record) -> list[str]:
    """The lines that tell the paths of a procedure apart. A guard's branches
    are left out: every case lists them all, but a branch one case built may read
    with this run's values (CHK-12)."""
    return [plain(line) for line in test_lines(case, symbolic=True, sections=("procedure",)) if line.branch is None]


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


# --------------------------------------------------------------------------- #
# A test's docstring (DOC-1)
# --------------------------------------------------------------------------- #

DOC_TITLE = {"objective": "Objective", "acceptance": "Acceptance criteria"}

_BULLET = re.compile(r"([-*])\s+(.*)")
_NUMBER = re.compile(r"(\d+)([.)])\s+(.*)")


@dataclass
class Block:
    """A paragraph (``kind == "p"``, one item) or a list (``"ul"``, ``"ol"``) of
    one section of a docstring. ``numbers`` are the author's (``ol`` only)."""

    kind: str
    items: list[str]
    numbers: list[int]


def doc_blocks(text: str) -> list[Block]:
    """Paragraphs and lists of one docstring section (``parse_docstring``).

    A line starting with ``-``, ``*``, ``1.`` or ``1)`` is a list item, indented
    or not (a nested list's items join the list). Lines of a paragraph, and lines
    under a list item (indented past its marker, or right after it and not left
    of it), are joined by a space; a blank line ends a paragraph, not a list. As
    in CommonMark, a numbered line right after paragraph text starts a list only
    at 1 (``... reaches\n512. Then`` is one paragraph)."""
    blocks: list[Block] = []
    marker = -1  # indent of the open list item's marker; -1: no open item
    gap = False  # a blank line since the last text
    for line in text.split("\n"):
        body = line.lstrip()
        if not body:
            gap = True
            continue
        indent = len(line) - len(body)
        bullet, number = _BULLET.fullmatch(body), _NUMBER.fullmatch(body)
        last = blocks[-1] if blocks else None
        if number and last is not None and last.kind == "p" and not gap and int(number.group(1)) != 1:
            number = None
        if bullet or number:
            kind, item = ("ul", bullet.group(2)) if bullet else ("ol", number.group(3))  # type: ignore[union-attr]
            if last is None or last.kind != kind:
                last = Block(kind, [], [])
                blocks.append(last)
            last.items.append(item.strip())
            if number:
                last.numbers.append(int(number.group(1)))
            marker = indent
        elif last is not None and (
            (marker >= 0 and (indent > marker or (not gap and indent >= marker)))
            or (last.kind == "p" and not gap)
        ):
            last.items[-1] += " " + body
        else:
            blocks.append(Block("p", [body], []))
            marker = -1
        gap = False
    return blocks


def case_doc(test: Record) -> Optional[dict[str, str]]:
    """A case's parsed docstring, when the record has one (schema 0.5)."""
    doc = test.get("doc")
    return doc if isinstance(doc, dict) and doc else None


def procedure_doc(proc: "Procedure") -> Optional[dict[str, str]]:
    """The docstring of the first case of a procedure that has one."""
    for case in proc.cases:
        doc = case_doc(case)
        if doc:
            return doc
    return None


def doc_text_lines(doc: dict[str, str], indent: str = "  ") -> list[str]:
    """A docstring as plain indented lines (the text renderer)."""
    out: list[str] = []
    for block in doc_blocks(doc.get("description", "")):
        out.extend(_plain_block(block, indent))
    for key in ("objective", "acceptance"):
        if not doc.get(key):
            continue
        blocks = doc_blocks(doc[key])
        if key == "objective" and len(blocks) == 1 and blocks[0].kind == "p":
            out.append(f"{indent}{DOC_TITLE[key]}: {blocks[0].items[0]}")
            continue
        out.append(f"{indent}{DOC_TITLE[key]}:")
        for block in blocks:
            out.extend(_plain_block(block, indent + "  "))
    return out


def _plain_block(block: Block, indent: str) -> list[str]:
    if block.kind == "p":
        return [indent + block.items[0]]
    if block.kind == "ul":
        return [f"{indent}- {item}" for item in block.items]
    return [f"{indent}{n}. {item}" for n, item in zip(block.numbers, block.items)]
