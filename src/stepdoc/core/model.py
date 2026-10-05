"""Data model of the run record (subset of brief §8 needed by the spike)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from .symbolic import Site, Resolution

Status = Literal["passed", "failed", "error", "not_run", "unknown"]


@dataclass
class Location:
    file: str
    line: int


@dataclass
class Action:
    target: str
    """Concrete target, e.g. ``"POST /users"`` or ``"dev.map.pulse.width"``."""
    op: str
    value: Any
    t: float
    meta: dict[str, Any] = field(default_factory=dict)

    # Filled by ``resolve()``; ``None`` until then.
    expr: Optional[str] = None
    """Source expression of the value (``"width"``), or ``None`` for literals/unknown."""
    symbolic_value: Optional[str] = None
    """Value as rendered in the procedure: ``"<width>"``, ``"100"``, ``'{"name": <name>}'``."""
    symbolic_target: Optional[str] = None
    """Target as rendered in the procedure: ``"GET /users/<user_id>"``."""
    bound_to: Optional[str] = None
    """For reads assigned to a name (``vout = dev.adc.value``): ``"vout"``."""
    symbolic: bool = False
    """False when the source could not be found (SYM-5): render the concrete value."""
    location: Optional[Location] = None

    # Captured at event time, resolved lazily (cheap hot path).
    site: Optional[Site] = field(default=None, repr=False, compare=False)
    value_from: Any = field(default=None, repr=False, compare=False)
    target_fields: tuple[str, ...] = field(default=(), repr=False, compare=False)
    target_template: Optional[str] = field(default=None, repr=False, compare=False)

    def resolve(self) -> None:
        """Turn the captured call site into symbolic text. Idempotent."""
        site, self.site = self.site, None
        if site is None:
            return
        self.location = Location(site.filename, site.lineno)
        res: Resolution = site.resolution()
        if not res.found:
            self.symbolic_value = None
            self.symbolic_target = self.target
            return
        self.symbolic = True
        self.bound_to = res.bound_to
        sym = res.select_value(self.value_from)
        if sym is not None:
            self.expr = None if sym.literal else sym.source
            self.symbolic_value = sym.text
        if self.target_template is not None:
            parts = {}
            for name in self.target_fields:
                fsym = res.select_value((name,))
                parts[name] = fsym.raw if fsym is not None else str(self.meta.get(name))
            for k, v in self.meta.items():
                parts.setdefault(k, str(v))
            self.symbolic_target = self.target_template.format(**parts)
        else:
            self.symbolic_target = self.target


@dataclass
class Step:
    number: str
    title: str
    status: Status = "unknown"
    started: float = 0.0
    ended: float = 0.0
    actions: list[Action] = field(default_factory=list)
    children: list["Step"] = field(default_factory=list)
    error: Optional[str] = None
    section: Literal["setup", "procedure", "teardown"] = "procedure"
