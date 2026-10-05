"""JSON run record (OUT-1): the canonical output every renderer reads.

A record holds both documents. The procedure uses the ``symbolic`` fields, the
executed report uses the ``concrete`` ones, so both always come from the same
run. Times are seconds relative to the test's start (monotonic clock), plus one
wall-clock timestamp per test.
"""

from __future__ import annotations

import base64
import enum
import json
import math
import os
import platform
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Union

from .model import Action, Check, Location, Step
from .recorder import Recorder

SCHEMA_ID = "urn:stepdoc:schema:run-record:0.1"  # no published URL yet
SCHEMA_VERSION = "0.1"


def version() -> str:
    try:
        from importlib.metadata import version as _v

        return _v("stepdoc")
    except Exception:
        return "unknown"


def to_jsonable(value: Any) -> Any:
    """Concrete values as JSON. Anything JSON cannot hold becomes a tagged object
    (``{"$type": ..., "$repr": ...}``) so it is never lost or silently changed."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"$type": "float", "$repr": repr(value)}
    if isinstance(value, enum.Enum):
        return {"$type": _qualname(value), "$enum": value.name, "value": to_jsonable(value.value)}
    if isinstance(value, (bytes, bytearray)):
        return {"$type": "bytes", "$base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return {k: to_jsonable(v) for k, v in value.items()}
    return {"$type": _qualname(value), "$repr": repr(value)}


def _qualname(value: Any) -> str:
    t = type(value)
    return f"{t.__module__}.{t.__qualname__}"


class _Ctx:
    def __init__(self, start: float, root: str) -> None:
        self.start = start
        self.root = root

    def t(self, value: Optional[float]) -> Optional[float]:
        return None if value is None else round(value - self.start, 9)

    def loc(self, loc: Optional[Location]) -> Optional[dict[str, Any]]:
        if loc is None:
            return None
        return {"file": relative_path(loc.file, self.root), "line": loc.line}


def relative_path(path: str, root: str) -> str:
    """Paths relative to the project root, so records from different machines diff cleanly."""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:  # another drive on Windows
        return path
    return path if rel.startswith("..") else rel.replace(os.sep, "/")


def _action(a: Action, ctx: _Ctx) -> dict[str, Any]:
    return {
        "type": "action",
        "number": a.number,
        "op": a.op,
        "direction": a.direction,
        "target": {"concrete": a.target, "symbolic": a.symbolic_target},
        "value": {"concrete": to_jsonable(a.value), "symbolic": a.symbolic_value, "expr": a.expr},
        "bound_to": a.bound_to,
        "resolved": a.symbolic,
        "location": ctx.loc(a.location),
        "t": ctx.t(a.t),
        "meta": to_jsonable(a.meta),
    }


def _check(c: Check, ctx: _Ctx) -> dict[str, Any]:
    return {
        "type": "check",
        "number": c.number,
        "text": c.text,
        "passed": c.passed,
        "expected": to_jsonable(c.expected),
        "actual": to_jsonable(c.actual),
        "detail": c.detail,
        "selected": c.selected,
        "children": [_check(ch, ctx) for ch in c.children],
        "location": ctx.loc(c.location),
    }


def _step(s: Step, ctx: _Ctx) -> dict[str, Any]:
    return {
        "type": "step",
        "number": s.number,
        "title": s.title,
        "section": s.section,
        "status": s.status,
        "started": ctx.t(s.started),
        "ended": ctx.t(s.ended),
        "error": s.error,
        "location": ctx.loc(s.location),
        "entries": [_entry(e, ctx) for e in s.entries],
    }


def _entry(e: Union[Action, Check, Step], ctx: _Ctx) -> dict[str, Any]:
    if isinstance(e, Step):
        return _step(e, ctx)
    if isinstance(e, Check):
        return _check(e, ctx)
    return _action(e, ctx)


def case_record(
    rec: Recorder,
    *,
    root: Optional[str] = None,
    test_id: Optional[str] = None,
    procedure_id: Optional[str] = None,
    case: Optional[str] = None,
    params: Optional[dict[str, Any]] = None,
    outcome: Optional[str] = None,
    seed: Any = None,
) -> dict[str, Any]:
    """One test case. ``procedure_id`` groups cases that share a procedure (the
    test without its parameter id); ``outcome`` is the runner's verdict."""
    rec.resolve()
    ctx = _Ctx(rec.started, os.getcwd() if root is None else root)
    tid = test_id or rec.name
    return {
        "id": tid,
        "procedure_id": procedure_id or tid,
        "case": case,
        "status": rec.status,
        "outcome": outcome,
        "dry_run": rec.dry_run,
        "params": {k: to_jsonable(v) for k, v in (params or {}).items()},
        "seed": to_jsonable(seed),
        "started_at": datetime.fromtimestamp(rec.started_at, timezone.utc).isoformat(),
        "duration": ctx.t(rec.ended),
        "unscoped": [_entry(e, ctx) for e in rec.unscoped],
        "steps": [_step(s, ctx) for s in rec.steps],
    }


def run_record(tests: Iterable[dict[str, Any]], *, dry_run: bool = False) -> dict[str, Any]:
    """The file stepdoc writes: one run, many test cases."""
    return {
        "$schema": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "generator": {"name": "stepdoc", "version": version(), "python": platform.python_version()},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "tests": list(tests),
    }


def to_dict(rec: Recorder, root: Optional[str] = None) -> dict[str, Any]:
    """A run record holding a single recorder (scripts and tests without pytest)."""
    return run_record([case_record(rec, root=root)], dry_run=rec.dry_run)


def dumps(record: Union[Recorder, dict[str, Any]], root: Optional[str] = None, **kw: Any) -> str:
    kw.setdefault("indent", 2)
    kw.setdefault("ensure_ascii", False)
    data = to_dict(record, root) if isinstance(record, Recorder) else record
    return json.dumps(data, **kw)


def load(path: Union[str, "os.PathLike[str]"]) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported run record schema {data.get('schema_version')!r}")
    return data
