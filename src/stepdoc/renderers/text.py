"""Plain-text procedure and report (terminal, logs, tests)."""

from __future__ import annotations

from .common import OUTCOME_TEXT, Record, procedure_lines, procedures, report_lines, vector_note


def render_procedure(record: Record) -> str:
    out: list[str] = []
    for proc in procedures(record):
        out.append(proc.procedure_id)
        rows = proc.param_table
        if rows:
            out.append("Parameters" + (f' (strategy "{proc.strategy}")' if proc.strategy else "") + ":")
            width = max(len(n) for n, _, _ in rows)
            dwidth = max(len(d or "") for _, d, _ in rows)
            for name, description, values in rows:
                middle = f"{(description or '').ljust(dwidth)}   " if dwidth else ""
                out.append(f"  {name.ljust(width)}   {middle}{', '.join(values)}")
        if proc.setup is not None:
            out.extend(procedure_lines(proc.setup, ("setup",)))
        for lines, members in proc.variants:
            if len(proc.variants) > 1:
                out.append(f"Variant for: {', '.join(m['case'] or m['id'] for m in members)}")
            elif proc.setup is not None or proc.teardown is not None:
                out.append("Procedure")
            out.extend(lines)
        if proc.teardown is not None:
            out.extend(procedure_lines(proc.teardown, ("teardown",)))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_report(record: Record) -> str:
    out: list[str] = []
    for test in record["tests"]:
        verdict = "DRY-RUN" if test["dry_run"] else OUTCOME_TEXT.get(test["outcome"], test["status"].upper())
        out.append(f"{test['id']}   {verdict}")
        if test["params"]:
            note = vector_note(test)
            out.append("  " + ", ".join(f"{k}={v!r}" for k, v in test["params"].items()) + (f" ({note})" if note else ""))
        out.extend(report_lines(test))
        out.append("")
    return "\n".join(out).rstrip() + "\n"
