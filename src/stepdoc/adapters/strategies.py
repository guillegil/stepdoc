"""pytest-strategies adapter: the seed, the row a case runs, and parameter domains.

pytest-strategies puts a ``strategy`` mark holding the row's ``VectorInfo`` on
each generated test, whose ``to_dict()`` gives the strategy, the kind of row
(directed, random, …), its name and the run's seed (PAR-4). Parameter domains
and descriptions (PAR-3, DRY-4) come from ``pytest_strategy.export_strategies()``,
read once per session; when it fails, cases still get their seed and row.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

import pytest

from . import Adapter, Host


class StrategiesAdapter(Adapter):
    name = "strategies"
    plugins = ("pytest_strategy",)

    def __init__(self) -> None:
        self._export: Optional[dict[tuple[str, str], Mapping[str, Any]]] = None

    def activate(self, host: Host) -> Optional[object]:
        self._export = None
        return None

    def case(self, item: pytest.Item) -> dict[str, Any]:
        rows = [
            mark.args[0].to_dict()
            for mark in item.iter_markers("strategy")
            if mark.args and callable(getattr(mark.args[0], "to_dict", None))
        ]
        rows = [r for r in rows if isinstance(r, Mapping)]
        if not rows:
            return {}
        first = rows[0]
        extra: dict[str, Any] = {
            "seed": first.get("seed"),
            "vector": {
                "source": "strategy",
                "strategy": first.get("strategy"),
                "kind": first.get("kind"),
                "name": first.get("name"),
            },
        }
        info: dict[str, dict[str, Any]] = {}
        for row in rows:
            for arg in self._arguments(row):
                name = arg.get("name")
                if isinstance(name, str) and name not in info:
                    info[name] = {"domain": domain(arg), "description": arg.get("description")}
        if info:
            extra["param_info"] = info
        return extra

    def _arguments(self, row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        if self._export is None:
            self._export = _read_export()
        origin = str(row.get("origin") or "").rpartition(":")[0]
        entry = self._export.get((str(row.get("strategy")), origin))
        if entry is None:  # origin unknown: the only registration of that name
            same = [e for (n, _), e in self._export.items() if n == row.get("strategy")]
            entry = same[0] if len(same) == 1 else None
        args = (entry or {}).get("arguments") or []
        return [a for a in args if isinstance(a, Mapping)]


def _read_export() -> dict[tuple[str, str], Mapping[str, Any]]:
    """``(strategy name, file)`` -> the strategy's ``Parameter.to_dict()``."""
    try:
        from pytest_strategy import export_strategies  # type: ignore[import-not-found]

        doc = json.loads(export_strategies())
    except Exception:
        return {}
    found: dict[tuple[str, str], Mapping[str, Any]] = {}
    for entry in doc.get("strategies") or []:
        parameter = entry.get("parameter")
        if isinstance(parameter, Mapping):
            origin = entry.get("origin") or {}
            found[(str(entry.get("name")), str(origin.get("file") or ""))] = parameter
    return found


def _value(v: Any) -> str:
    """A value in pytest-strategies' encoding, as text."""
    if isinstance(v, Mapping):
        if "member" in v:
            return str(v["member"])
        if "$float" in v:
            return str(v["$float"])
        if "$repr" in v:
            return str(v["$repr"])
    return repr(v) if isinstance(v, str) else str(v)


_NUMBER_TYPES = {"RNGInteger": "integer", "RNGFloat": "float",
                 "RNGWeightedInteger": "integer", "RNGWeightedFloat": "float"}


def domain(arg: Mapping[str, Any]) -> Optional[str]:
    """The set of values an argument draws from, in words: ``integer 0 … 4095``."""
    if arg.get("source") == "value":
        return f"fixed {_value(arg.get('value'))}"
    rng = arg.get("rng")
    if not isinstance(rng, Mapping):
        return None
    kind = str(rng.get("type") or "")
    word = _NUMBER_TYPES.get(kind)
    if word and "min" in rng:
        return f"{word} {_value(rng['min'])} … {_value(rng['max'])}"
    if word and rng.get("ranges"):
        parts = [f"{_value(r['min'])} … {_value(r['max'])}" for r in rng["ranges"]]
        return f"{word} " + ", ".join(parts)
    if "members" in rng:
        return f"{rng.get('enum', 'enum')}: " + ", ".join(_value(m) for m in rng["members"])
    if "sequence" in rng:
        word = "series" if kind == "Series" else "one of"
        return f"{word} " + ", ".join(_value(v) for v in rng["sequence"])
    if "choices" in rng:
        return "one of " + ", ".join(_value(v) for v in rng["choices"])
    if "true_probability" in rng:
        return "boolean"
    if "min_length" in rng or "length" in rng:
        if rng.get("length") is not None:
            return f"string, {rng['length']} characters"
        return f"string, {rng.get('min_length')} … {rng.get('max_length')} characters"
    return kind or None
