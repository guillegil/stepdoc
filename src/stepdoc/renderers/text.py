"""Plain-text procedure and report, just enough to read the spike's output.

Op-specific wording ("=" for writes) is a placeholder for ACT-6 formatting.
"""

from __future__ import annotations

from typing import Any

from ..core.model import Action, Step
from ..core.recorder import Recorder


def _concrete(value: Any) -> str:
    return repr(value)


def _line(a: Action, symbolic: bool) -> str:
    if symbolic:
        target = a.symbolic_target or a.target
        if a.op == "read":
            # A read's value is an outcome, never part of the procedure.
            value = f"<{a.bound_to}>" if a.bound_to else ""
        elif a.symbolic_value is not None:
            value = a.symbolic_value
        elif a.value is None:
            value = ""
        else:
            value = _concrete(a.value) + ("" if a.symbolic else "  (concrete)")
    else:
        target = a.target
        value = "" if a.value is None else _concrete(a.value)
    if a.op == "write":
        return f"{target} = {value}"
    if a.op == "read":
        return f"Read {target}" + (f" -> {value}" if value else "")
    return f"{target}  {value}".rstrip()


def _steps(steps: list[Step], symbolic: bool, depth: int, out: list[str]) -> None:
    for s in steps:
        status = "" if symbolic else f"   [{s.status}]"
        out.append(f"{'   ' * depth}{s.number}. {s.title}{status}")
        for e in s.entries:
            if isinstance(e, Step):
                _steps([e], symbolic, depth + 1, out)
                continue
            extra = ""
            if not symbolic and "status" in e.meta:
                extra = f"  -> {e.meta['status']}"
            out.append(f"{'   ' * (depth + 1)}{e.number}. {_line(e, symbolic)}{extra}")


def render(rec: Recorder, *, symbolic: bool, title: str = "") -> str:
    rec.resolve()
    out = [title] if title else []
    if rec.unscoped:
        out.append("Unscoped")
        for a in rec.unscoped:
            out.append(f"   - {_line(a, symbolic)}")
    _steps(rec.steps, symbolic, 0, out)
    return "\n".join(out)


def render_procedure(rec: Recorder, title: str = "") -> str:
    return render(rec, symbolic=True, title=title)


def render_report(rec: Recorder, title: str = "") -> str:
    return render(rec, symbolic=False, title=title)
