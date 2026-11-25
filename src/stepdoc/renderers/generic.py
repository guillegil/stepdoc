"""The generic procedure as JSON (OUT-7): one entry per test, whatever number of
cases (parameter sets, strategy vectors) it ran. Built from the same grouping
and lines as the other procedure renderers (``procedures``, ``step_lines``,
``check_line``…), so its text is the procedure's, line for line."""

from __future__ import annotations

import json
import posixpath
from typing import Optional

from .common import (
    Line,
    Procedure,
    Record,
    action_line,
    check_line,
    line_body,
    procedure_doc,
    procedure_lines,
    procedures,
    repeat_line,
    short_name,
    step_lines,
)

SCHEMA_VERSION = "1.0"
SCHEMA_ID = f"urn:stepdoc:schema:generic:{SCHEMA_VERSION}"  # schema/generic.schema.json


def render_generic(record: Record) -> str:
    """The generic procedure of every test in ``record`` as a JSON document."""
    doc = {
        "$schema": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "generator": record.get("generator"),
        "created_at": record.get("created_at"),
        "tests": [_test(proc) for proc in procedures(record)],
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def _test(proc: Procedure) -> Record:
    test_id = proc.procedure_id
    path = test_id.split("::", 1)[0]
    out: Record = {
        "test_id": test_id,
        "test_name": short_name(test_id),
        "test_file": posixpath.basename(path),
        "test_path": path,
    }
    doc = procedure_doc(proc)
    if doc:
        out["doc"] = dict(doc)
    rows = proc.param_table
    if rows:
        out["parameters"] = [
            {"name": name, "description": description, "values": values}
            for name, description, values in rows
        ]
        if proc.strategy:
            out["strategy"] = proc.strategy
    out["cases"] = len(proc.cases)

    steps: list[Record] = []
    for section, case in (("precondition", proc.precondition), ("setup", proc.setup)):
        if case is not None:
            steps.extend(_section(case, section))
    steps.extend(_section(_follower(*proc.variants[0]), "procedure"))
    if proc.teardown is not None:
        steps.extend(_section(proc.teardown, "teardown"))
    out["steps"] = steps
    if len(proc.variants) > 1:
        # The code took different paths for different values: each path once,
        # with the cases that took it (the procedure section only).
        out["variants"] = [
            {"cases": [m["case"] or m["id"] for m in members], "steps": _section(_follower(lines, members), "procedure")}
            for lines, members in proc.variants
        ]
    return out


def _follower(lines: list[str], members: list[Record]) -> Record:
    """The case whose procedure section is the variant's: a case that stopped
    early (a failure) follows a longer one (``procedures``)."""
    for case in members:
        if procedure_lines(case, ("procedure",)) == lines:
            return case
    return members[0]  # pragma: no cover - a variant's lines are one of its cases'


def _section(test: Record, section: str) -> list[Record]:
    loose = [e for e in test["unscoped"] if e.get("section", "procedure") == section]
    steps = [s for s in test["steps"] if s.get("section", "procedure") == section]
    return [*_entries(loose, section), *(_step(s, section) for s in steps)]


def _entries(entries: list[Record], section: str) -> list[Record]:
    out: list[Record] = []
    for e in entries:
        kind = e["type"]
        if kind == "step":
            out.append(_step(e, section))
        elif kind == "repeat":
            node = _node("repeat", repeat_line(e, True, 0), section)
            node["steps"] = _entries(e["entries"], section)
            out.append(node)
        elif kind == "check":
            out.append(_node("check", check_line(e, True, 0), section))
            # Folded checks (CHK-11) list their failed items only in the report;
            # other children are part of the check (a composite's branches).
            if not e.get("each") and e.get("children"):
                out[-1]["steps"] = [_node("check", check_line(c, True, 0), section) for c in e["children"]]
        else:
            out.append(_node("action", action_line(e, True, 0), section))
    return out


def _step(s: Record, section: str) -> Record:
    node = _node("step", next(step_lines(s, True)), section)
    if s.get("applied_in"):
        node["applied_in"] = s["applied_in"]
    node["steps"] = _entries(s["entries"], section)
    return node


def _node(kind: str, line: Line, section: str) -> Record:
    node: Record = {"kind": kind, "number": line.number, "section": section, "text": line_body(line)}
    if kind == "step" or kind == "repeat":
        if line.remark:
            node["note"] = line.remark
        return node
    for key, value in (("verb", line.verb), ("label", line.label), ("code", line.code)):
        if value:
            node[key] = value
    if line.result:
        node["result"] = line.result  # a read bound to a name: <rdata>
    note: Optional[str] = line.aside if kind == "check" else line.note
    if note:
        node["note"] = note
    return node


