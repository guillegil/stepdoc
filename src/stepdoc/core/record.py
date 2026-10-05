"""JSON run record (OUT-1): the canonical output every renderer reads.

One record holds both documents. The procedure uses the ``symbolic`` fields,
the executed report uses the ``concrete`` ones, so both always come from the
same run. Times are seconds relative to the run's start (monotonic clock),
plus one wall-clock timestamp for the run itself.
"""

from __future__ import annotations

import base64
import enum
import json
import math
import os
import platform
from datetime import datetime, timezone
from typing import Any, Optional, Union

from .model import Action, Step
from .recorder import Recorder

SCHEMA_ID = "urn:stepdoc:schema:run-record:0.1"  # no published URL yet
SCHEMA_VERSION = "0.1"


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("stepdoc")
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


def _seconds(t: Optional[float], start: float) -> Optional[float]:
    return None if t is None else round(t - start, 9)


def _file(path: str, root: str) -> str:
    """Paths relative to the project root, so records from different machines diff cleanly."""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:  # another drive on Windows
        return path
    return path if rel.startswith("..") else rel.replace(os.sep, "/")


def _action(a: Action, start: float, root: str) -> dict[str, Any]:
    return {
        "type": "action",
        "number": a.number,
        "op": a.op,
        "target": {"concrete": a.target, "symbolic": a.symbolic_target},
        "value": {
            "concrete": to_jsonable(a.value),
            "symbolic": a.symbolic_value,
            "expr": a.expr,
        },
        "bound_to": a.bound_to,
        "resolved": a.symbolic,
        "location": None if a.location is None else {"file": _file(a.location.file, root), "line": a.location.line},
        "t": _seconds(a.t, start),
        "meta": to_jsonable(a.meta),
    }


def _step(s: Step, start: float, root: str) -> dict[str, Any]:
    return {
        "type": "step",
        "number": s.number,
        "title": s.title,
        "section": s.section,
        "status": s.status,
        "started": _seconds(s.started, start),
        "ended": _seconds(s.ended, start),
        "error": s.error,
        "entries": [_entry(e, start, root) for e in s.entries],
    }


def _entry(e: Union[Action, Step], start: float, root: str) -> dict[str, Any]:
    return _step(e, start, root) if isinstance(e, Step) else _action(e, start, root)


def _status(steps: list[Step]) -> str:
    statuses = {s.status for s in steps}
    for st in ("error", "failed", "unknown"):
        if st in statuses:
            return st
    return "passed" if steps else "unknown"


def to_dict(rec: Recorder, root: Optional[str] = None) -> dict[str, Any]:
    """``root``: project root that source paths are made relative to (default: cwd)."""
    rec.resolve()
    start = rec.started
    root = os.getcwd() if root is None else root
    return {
        "$schema": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "generator": {"name": "stepdoc", "version": _version(), "python": platform.python_version()},
        "test": {
            "id": rec.name,
            "status": _status(rec.steps),
            "started_at": datetime.fromtimestamp(rec.started_at, timezone.utc).isoformat(),
            "duration": _seconds(rec.ended, start),
            "params": {},
            "seed": None,
        },
        "unscoped": [_action(a, start, root) for a in rec.unscoped],
        "steps": [_step(s, start, root) for s in rec.steps],
    }


def dumps(rec: Recorder, root: Optional[str] = None, **kw: Any) -> str:
    kw.setdefault("indent", 2)
    kw.setdefault("ensure_ascii", False)
    return json.dumps(to_dict(rec, root), **kw)
