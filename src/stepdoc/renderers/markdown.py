"""Markdown procedure and report (OUT-2), rendered from the JSON run record."""

from __future__ import annotations

from .common import (
    OUTCOME_TEXT,
    STATUS_MARK,
    Line,
    Record,
    procedures,
    short_name,
    show,
    test_lines,
)


def _code(text: str) -> str:
    if not text:
        return ""
    fence = "``" if "`" in text else "`"
    pad = " " if fence == "``" else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _md_line(line: Line) -> str:
    indent = "  " * line.depth
    if line.is_step:
        head = f"**{line.number}. {line.code}**" if line.number else f"**{line.code}**"
        parts = [head]
    else:
        num = f"{line.number}. " if line.number else ""
        parts = [num + " ".join(p for p in (line.verb, _code(line.code)) if p)]
        if line.result:
            parts.append(f"→ {_code(line.result)}")
    if line.mark:
        parts.append(line.mark)
    if line.note:
        parts.append(f"_{line.note}_")
    return f"{indent}- " + " ".join(parts)


def render_procedure(record: Record, title: str = "Test procedures") -> str:
    out = [f"# {title}", ""]
    for proc in procedures(record):
        n = len(proc.cases)
        out += [f"## {short_name(proc.procedure_id)}", "", f"_{proc.procedure_id} · {n} case{'s' * (n != 1)}_", ""]
        params = proc.params
        if params:
            out += ["**Parameters**", "", "| Parameter | Values |", "|---|---|"]
            for name, values in params.items():
                out.append(f"| {_code(name)} | {', '.join(_code(v) for v in values)} |")
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
    out += ["| Test | Outcome | Steps |", "|---|---|---|"]
    for t in tests:
        verdict = OUTCOME_TEXT.get(t["outcome"], "?")
        out.append(f"| {_code(t['id'])} | {verdict} | {STATUS_MARK.get(t['status'], '?')} |")
    out.append("")
    for t in tests:
        verdict = "DRY RUN" if t["dry_run"] else OUTCOME_TEXT.get(t["outcome"], "?")
        out += [f"## {short_name(t['id'])} — {verdict}", ""]
        meta = [t["id"], t["started_at"]]
        if t["duration"] is not None:
            meta.append(f"{t['duration'] * 1000:.0f} ms")
        if t["seed"] is not None:
            meta.append(f"seed {show(t['seed'])}")
        out += [f"_{' · '.join(meta)}_", ""]
        if t["params"]:
            out += ["Parameters: " + ", ".join(_code(f"{k} = {show(v)}") for k, v in t["params"].items()), ""]
        out += [_md_line(line) for line in test_lines(t, symbolic=False)]
        out.append("")
    return "\n".join(out).rstrip() + "\n"
