"""Markdown procedure and report (OUT-2), rendered from the JSON run record."""

from __future__ import annotations

import re
from typing import Optional

from ..core.fmt import spans
from .common import (
    DOC_TITLE,
    OUTCOME_TEXT,
    STATUS_MARK,
    Block,
    Line,
    Record,
    anchors,
    case_doc,
    doc_blocks,
    procedure_doc,
    procedures,
    short_name,
    show,
    test_lines,
    vector_note,
)


def _code(text: str) -> str:
    if not text:
        return ""
    # A fence longer than any run of backticks inside (an operand name may have some).
    runs = re.findall(r"`+", text)
    fence = "`" * (max(map(len, runs), default=0) + 1)
    pad = " " if runs else ""
    return f"{fence}{pad}{text}{pad}{fence}"


_PROSE = str.maketrans({c: "\\" + c for c in "\\`*_[]<>|"})


def _prose(text: str) -> str:
    """Plain words in Markdown: one line, characters that would format escaped."""
    return " ".join(text.split()).translate(_PROSE)


def _words(text: str) -> str:
    """Docstring prose: escaped like other prose, its `code` spans kept as code,
    and nothing at the start of the paragraph that would make it a heading or a list."""
    out = "".join(_code(t) if code else t.translate(_PROSE) for t, code in spans(" ".join(text.split())))
    return "\\" + out if out[:1] in ("#", "+", "-", "=") else out


def _md_block(block: Block) -> list[str]:
    if block.kind == "p":
        return [_words(block.items[0])]
    if block.kind == "ul":
        return [f"- {_words(item)}" for item in block.items]
    return [f"{n}. {_words(item)}" for n, item in zip(block.numbers, block.items)]


def _md_doc(doc: Optional[dict[str, str]]) -> list[str]:
    """DOC-1: description, objective and acceptance criteria, each block followed
    by a blank line."""
    if not doc:
        return []
    out: list[str] = []
    for block in doc_blocks(doc.get("description", "")):
        out += _md_block(block) + [""]
    for key in ("objective", "acceptance"):
        if not doc.get(key):
            continue
        blocks = doc_blocks(doc[key])
        if key == "objective" and len(blocks) == 1 and blocks[0].kind == "p":
            out += [f"**{DOC_TITLE[key]}.** {_words(blocks[0].items[0])}", ""]
            continue
        out += [f"**{DOC_TITLE[key]}**", ""]
        for block in blocks:
            out += _md_block(block) + [""]
    return out


def _md_line(line: Line) -> str:
    indent = "  " * line.depth
    if line.is_step:
        head = f"**{line.number}. {line.code}**" if line.number else f"**{line.code}**"
        parts = [head]
    else:
        num = f"{line.number}. " if line.number else ""
        label = _prose(line.label) if line.label else ""
        parts = [num + " ".join(p for p in (line.verb, label, _code(line.code)) if p)]
        if line.aside:
            parts.append(f"({_prose(line.aside)})")
        if line.result:
            parts.append(f"→ {_code(line.result)}")
    if line.mark:
        parts.append(line.mark)
    if line.note:
        parts.append(f"_{line.note}_")
    text = f"{indent}- " + " ".join(parts)
    if line.remark:  # under the item, as its own paragraph
        text += "\n\n" + "\\\n".join(f"{indent}  > {r}" for r in line.remark.splitlines())
    return text


def render_procedure(record: Record, title: str = "Test procedures") -> str:
    out = [f"# {title}", ""]
    procs = procedures(record)
    ids = anchors([p.procedure_id for p in procs])
    if len(procs) > 1:  # OUT-5: contents with test ids
        out += ["**Contents**", ""]
        out += [f"{i}. [{short_name(p.procedure_id)}](#{a}) · {_code(p.procedure_id)}" for i, (a, p) in enumerate(zip(ids, procs), 1)]
        out.append("")
    for anchor, proc in zip(ids, procs):
        n = len(proc.cases)
        out += [
            f'<a id="{anchor}"></a>',
            "",
            f"## {short_name(proc.procedure_id)}",
            "",
            f"_{proc.procedure_id} · {n} case{'s' * (n != 1)}_",
            "",
        ]
        out += _md_doc(procedure_doc(proc))
        rows = proc.param_table
        if rows:
            heading = "**Parameters**" + (f" (strategy {_code(proc.strategy)})" if proc.strategy else "")
            described = any(d for _, d, _ in rows)
            out += [heading, ""]
            if described:
                out += ["| Parameter | Description | Values |", "|---|---|---|"]
            else:
                out += ["| Parameter | Values |", "|---|---|"]
            for name, description, values in rows:
                cells = [_code(name)] + ([description or ""] if described else []) + [", ".join(_code(v) for v in values)]
                out.append("| " + " | ".join(cells) + " |")
            out.append("")
        if proc.setup is not None:
            out += [_md_line(line) for line in test_lines(proc.setup, symbolic=True, sections=("setup",))]
            out.append("")
        for _, members in proc.variants:
            if len(proc.variants) > 1:
                cases = ", ".join(_code(m["case"] or m["id"]) for m in members)
                out += [f"**Procedure for {cases}**", ""]
            else:
                out += ["**Procedure**", ""]
            only = ("procedure",)
            longest = max(members, key=lambda m: len(test_lines(m, symbolic=True, sections=only)))
            out += [_md_line(line) for line in test_lines(longest, symbolic=True, sections=only)]
            out.append("")
        if proc.teardown is not None:
            out += [_md_line(line) for line in test_lines(proc.teardown, symbolic=True, sections=("teardown",))]
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_report(record: Record, title: str = "Test report") -> str:
    tests = record["tests"]
    out = [f"# {title}", ""]
    if record.get("dry_run"):
        out += ["_Dry run: results were not judged._", ""]
    ids = anchors([t["id"] for t in tests])
    out += ["| Test | Outcome | Steps |", "|---|---|---|"]
    for anchor, t in zip(ids, tests):
        verdict = OUTCOME_TEXT.get(t["outcome"], "?")
        out.append(f"| [{_code(t['id'])}](#{anchor}) | {verdict} | {STATUS_MARK.get(t['status'], '?')} |")
    out.append("")
    for anchor, t in zip(ids, tests):
        verdict = "DRY RUN" if t["dry_run"] else OUTCOME_TEXT.get(t["outcome"], "?")
        out += [f'<a id="{anchor}"></a>', "", f"## {short_name(t['id'])} — {verdict}", ""]
        meta = [t["id"], t["started_at"]]
        if t["duration"] is not None:
            meta.append(f"{t['duration'] * 1000:.0f} ms")
        if t["seed"] is not None:
            meta.append(f"seed {show(t['seed'])}")
        out += [f"_{' · '.join(meta)}_", ""]
        out += _md_doc(case_doc(t))
        if t["params"]:
            note = vector_note(t)
            line = "Parameters: " + ", ".join(_code(f"{k} = {show(v)}") for k, v in t["params"].items())
            out += [line + (f" ({note})" if note else ""), ""]
        out += [_md_line(line) for line in test_lines(t, symbolic=False)]
        out.append("")
    return "\n".join(out).rstrip() + "\n"
