"""Plain-text procedure and report (terminal, logs, tests)."""

from __future__ import annotations

from .common import OUTCOME_TEXT, Record, procedures, report_lines


def render_procedure(record: Record) -> str:
    out: list[str] = []
    for proc in procedures(record):
        out.append(proc.procedure_id)
        params = proc.params
        if params:
            out.append("Parameters:")
            width = max(len(n) for n in params)
            for name, values in params.items():
                out.append(f"  {name.ljust(width)}   {', '.join(values)}")
        for lines, members in proc.variants:
            if len(proc.variants) > 1:
                out.append(f"Variant for: {', '.join(m['case'] or m['id'] for m in members)}")
            out.extend(lines)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_report(record: Record) -> str:
    out: list[str] = []
    for test in record["tests"]:
        verdict = "DRY-RUN" if test["dry_run"] else OUTCOME_TEXT.get(test["outcome"], test["status"].upper())
        out.append(f"{test['id']}   {verdict}")
        if test["params"]:
            out.append("  " + ", ".join(f"{k}={v!r}" for k, v in test["params"].items()))
        out.extend(report_lines(test))
        out.append("")
    return "\n".join(out).rstrip() + "\n"
