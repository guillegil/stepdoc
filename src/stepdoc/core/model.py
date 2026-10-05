"""Data model of a recorded run (brief §8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union

from .symbolic import Resolution, Site

Status = Literal["passed", "failed", "error", "not_run", "unknown"]
Direction = Literal["in", "out", "exchange"]
Section = Literal["setup", "procedure", "teardown"]

# Used when a bridge does not say which way data flows. Unknown ops are "in".
DEFAULT_DIRECTIONS: dict[str, Direction] = {
    "read": "out",
    "get": "out",
    "measure": "out",
    "receive": "out",
    "consume": "out",
    "request": "exchange",
    "query": "exchange",
    "call": "exchange",
}


@dataclass
class Location:
    file: str
    line: int


@dataclass
class Action:
    target: str
    """Concrete target, e.g. ``"POST /users"`` or ``"dev.map.pulse.width"``."""
    op: str
    """Free-form: ``"request"``, ``"write"``, ``"read"``, ``"query"``, ``"measure"``…"""
    value: Any
    t: float
    direction: Direction = "in"
    """``in``: the test sends ``value``; ``out``: ``value`` is a result; ``exchange``: both
    (``value`` is what was sent, the response lives in ``meta``)."""
    meta: dict[str, Any] = field(default_factory=dict)
    number: Optional[str] = None
    """Position in the step tree (``"2.1"``); ``None`` for unscoped actions."""
    count: int = 1
    """How many identical consecutive events this entry stands for (ACT-4 polling)."""
    t_last: Optional[float] = None
    """Time of the last repeated event when ``count > 1``."""
    section: Optional[Section] = None
    """Set on unscoped actions only: the phase (setup, procedure, teardown) they ran in."""
    name: Optional[str] = None
    """Symbolic name given with ``stepdoc.value()`` (SYM-4); wins over the source."""

    # Filled by ``resolve()``.
    expr: Optional[str] = None
    """Source expression of the value (``"width"``), or ``None`` for literals/unknown."""
    symbolic_value: Optional[str] = None
    """Value as rendered in the procedure: ``"<width>"``, ``"100"``, ``'{"name": <name>}'``."""
    symbolic_target: Optional[str] = None
    """Target as rendered in the procedure: ``"GET /users/<user_id>"``."""
    bound_to: Optional[str] = None
    """For results assigned to a name (``vout = dev.adc.value``): ``"vout"``."""
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
        if self.name is not None and self.direction != "out":
            self.symbolic_value = f"<{self.name}>"
            self.expr = self.name
        if site is None:
            if self.symbolic_target is None:
                self.symbolic_target = self.target
            return
        self.location = Location(site.filename, site.lineno)
        res: Resolution = site.resolution()
        if not res.found:
            self.symbolic_target = self.target
            return
        self.symbolic = True
        self.bound_to = res.bound_to
        if self.direction != "out" and self.name is None:
            sym = res.select_value(self.value_from)
            if sym is not None:
                self.expr = None if sym.literal else sym.source
                self.symbolic_value = sym.text
        if self.target_template is not None:
            parts: dict[str, str] = {}
            for name in self.target_fields:
                fsym = res.select_value((name,))
                parts[name] = fsym.raw if fsym is not None else str(self.meta.get(name))
            for k, v in self.meta.items():
                parts.setdefault(k, str(v))
            self.symbolic_target = self.target_template.format(**parts)
        else:
            self.symbolic_target = self.target


@dataclass
class Check:
    text: str
    passed: Optional[bool]
    """``None``: recorded but not judged (CHK-7), e.g. in dry-run."""
    expected: Any = None
    actual: Any = None
    detail: Optional[str] = None
    children: list["Check"] = field(default_factory=list)
    selected: Optional[str] = None
    """Chosen branch of a composite check (CHK-6)."""
    location: Optional[Location] = None
    source: Any = field(default=None, repr=False, compare=False)
    """Original object from the producer; opaque to the core and never serialised."""
    number: Optional[str] = None
    reads: list[Action] = field(default_factory=list)
    """Reads on the same source line that fed this check (ACT-5), e.g. the register
    read by ``assert dev.status.ok == 1``. They are shown on the check's line."""
    section: Optional[Section] = None
    """Set on unscoped checks only, like ``Action.section``."""


Entry = Union[Action, Check, "Step"]


@dataclass
class Step:
    number: str
    title: str
    section: Section = "procedure"
    status: Status = "unknown"
    started: float = 0.0
    ended: float = 0.0
    entries: list[Entry] = field(default_factory=list)
    """Actions, checks and child steps in execution order. They share one numbering
    (1.1, 1.2, 1.2.1)."""
    error: Optional[str] = None
    location: Optional[Location] = None

    @property
    def actions(self) -> list[Action]:
        return [e for e in self.entries if isinstance(e, Action)]

    @property
    def checks(self) -> list[Check]:
        return [e for e in self.entries if isinstance(e, Check)]

    @property
    def children(self) -> list["Step"]:
        return [e for e in self.entries if isinstance(e, Step)]


_RANK = {"passed": 0, "not_run": 0, "unknown": 1, "failed": 2, "error": 3}


def combine(statuses: list[Status]) -> Status:
    """STEP-6: worst wins; ``error`` > ``failed`` > ``unknown`` > ``passed``."""
    worst: Status = "passed"
    for s in statuses:
        if _RANK[s] > _RANK[worst]:
            worst = s
    return worst


def check_status(c: Check) -> Status:
    if c.passed is None:
        return "passed"  # documentation only, never turns a step red or green
    return "passed" if c.passed else "failed"
