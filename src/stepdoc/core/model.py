"""Data model of a recorded run (brief §8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union

from .symbolic import Params, Resolution, Site, apply_target_pattern, constant_text

Status = Literal["passed", "failed", "error", "not_run", "unknown"]
Direction = Literal["in", "out", "exchange"]
Section = Literal["precondition", "setup", "procedure", "teardown"]
"""``precondition`` holds the blocks made with ``stepdoc.precondition()`` (PRE-1),
whichever phase they ran in; the others follow the phase (STEP-8)."""

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
    read_expr: Optional[str] = field(default=None, repr=False, compare=False)
    """For results: source of the expression that produced the value (``dat.txfe``),
    so a check on the same line can tell which read is its checked value."""

    # Captured at event time, resolved lazily (cheap hot path).
    site: Optional[Site] = field(default=None, repr=False, compare=False)
    value_from: Any = field(default=None, repr=False, compare=False)
    target_fields: tuple[str, ...] = field(default=(), repr=False, compare=False)
    target_template: Optional[str] = field(default=None, repr=False, compare=False)

    def resolve(self, params: Params = None) -> None:
        """Turn the captured call site into symbolic text. Idempotent. ``params``
        are the names of the test's parameters: only values that come from them
        stay generic (SYM-9)."""
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
        if self.direction == "out":
            self.read_expr = res.node_source
        if self.direction != "out" and self.name is None:
            sym = res.select_value(self.value_from, params)
            if sym is not None:
                self.expr = None if sym.literal else sym.source
                self.symbolic_value = sym.text
                if sym.constant:
                    self.symbolic_value = constant_text(sym, self.value) or sym.text
        if self.target_template is not None:
            parts: dict[str, str] = {}
            for name in self.target_fields:
                fsym = res.select_value((name,), params)
                if fsym is not None and fsym.constant and name in self.meta:
                    shown = constant_text(fsym, self.meta[name], raw=True)
                    parts[name] = fsym.raw if shown is None else shown
                else:
                    parts[name] = fsym.raw if fsym is not None else str(self.meta.get(name))
            for k, v in self.meta.items():
                parts.setdefault(k, str(v))
            self.symbolic_target = self.target_template.format(**parts)
        else:
            self.symbolic_target = self.target
            if res.target_pattern is not None:
                self.symbolic_target = apply_target_pattern(self.target, res.target_pattern) or self.target


@dataclass
class Each:
    """One check made for every item of a collection, shown as one line (CHK-11).

    pytest-verifier's ``all_satisfy`` makes one child check per item. When every
    child reads the same (same name, same code), the check becomes one line,
    ``'Index error': for each w in waveforms, w & INDEX_ERROR_BIT == 0``, and only
    the failed children are kept, each with its ``index``.
    """

    count: int
    """Items checked."""
    failed: int
    """Items whose check failed (they are the check's ``children``)."""
    item: Optional[str] = None
    """How the test names one item (the factory's parameter, ``w``), when known."""
    items: Optional[str] = None
    """How the test names the collection (``waveforms``), when known."""
    values: list[Any] = field(default_factory=list)
    """The value each item's check got, in item order; at most ``EACH_VALUES``
    (the first and the last half) when there are more."""


#: Most item values an ``Each`` keeps: the first and the last half of them.
EACH_VALUES = 64


@dataclass
class Branch:
    """One branch of a guarded check (pytest-verifier's ``guard``): an if/elif/else
    chain, of which the first branch whose condition holds is checked (CHK-12)."""

    label: Optional[str]
    """The name the test gave the branch (``"fast mode"``); None for the default."""
    condition: Optional[str] = None
    """The condition's source, as written (``mode == "fast"``), when it was found."""
    holds: Optional[bool] = None
    """The condition's truth in this run; None when it was not evaluated (an
    earlier branch was taken) or for the default."""
    taken: bool = False
    """Whether this run checked this branch."""
    default: bool = False
    """The ``default=`` branch, checked when no condition holds."""
    check: Optional["Check"] = None
    """The branch's check, when it was built (a lazy check is built only when its
    branch is taken)."""
    source: Optional[str] = None
    """The source of a check that was not built (``verify.equal(speed, 40,
    name="Speed")``), so the procedure can still say what the branch checks."""


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
    symbolic_text: Optional[str] = None
    """Text for the procedure when it differs from ``text``: an adapter's check whose
    expected value came from a name (``'Stored age' == <age>``) or whose checked
    value is a register with variable parts (``dev.blk.reg<ch>.ready == 1``)."""
    note: Optional[str] = None
    """Words for the reader, shown after the check's text in parentheses: the
    producer's ``note=``, else a source comment at ``location`` (CHK-9)."""
    label: Optional[str] = None
    """The name given to the check, when ``text`` (and ``symbolic_text``) start
    with it in quotes (``'FIFO not full': dev.ctl.tx == 0``, ``'HTTP status' ==
    201``): what follows it is code, the name is words (CHK-10)."""
    operands: Optional[dict[str, dict[str, str]]] = None
    """Names the test gave the compared values (pytest-verifier's ``actual_name=``
    and ``expected_name=``), which word the check: ``{"actual": {"concrete":
    "dev.reg3.F", "symbolic": "dev.reg<channel>.F"}, "expected": {...}}``, each
    key only when given. ``symbolic`` is the name with its variable parts as
    ``<expr>`` (the same text when it has none)."""
    each: Optional[Each] = None
    """Set when this check stands for one check per item, folded into one line
    (CHK-11): ``children`` then holds only the items that failed."""
    index: Optional[int] = None
    """For a child of a folded check: the item's position, from 0."""
    branches: Optional[list[Branch]] = None
    """Set on a guarded check (CHK-12): every branch of its if/elif/else chain, in
    order, the default last."""
    note_from_comment: bool = field(default=False, repr=False, compare=False)
    """Look up ``note`` from a source comment when the recorder resolves (asserts
    and adapters' checks set it; ``stepdoc_comment_notes = false`` turns it off)."""


@dataclass
class Repeat:
    """Passes of a loop that did the same thing each time (ACT-10).

    Recorded automatically when a group of actions repeats back to back (a ``for``
    or ``while`` loop), or by ``with stepdoc.repeat(until=...)``. ``entries`` is one
    pass, with the values of the last one; ``count`` is how many passes it stands
    for. A pass that did something else is not counted: it follows as ordinary
    entries, so a failure inside the loop stays visible.
    """

    entries: list["Entry"]
    count: Optional[int]
    """Passes, or ``None`` when a ``repeat()`` block's passes could not be told apart
    (they differed, or the body did more than collapses on its own)."""
    t: float
    t_last: Optional[float] = None
    """Time of the last action of the last pass."""
    until: Optional[str] = None
    """Exit condition in words, from ``stepdoc.repeat(until=...)``."""
    note: Optional[str] = None
    """Why, or anything the reader should know, from ``repeat(..., note=...)``."""
    counter: Optional[str] = None
    """The test's variable that counts the passes, from ``repeat(..., counter=...)``."""
    inc: Union[int, float, None] = None
    """How much ``counter`` grows each pass, from ``repeat(..., inc=...)``."""
    number: Optional[str] = None
    section: Optional[Section] = None
    """Set on unscoped repeats only, like ``Action.section``."""
    partial: Optional[list[Any]] = field(default=None, repr=False, compare=False)
    """The pass in progress while the loop runs; ``None`` once the repeat is closed."""

    @property
    def location(self) -> Optional[Location]:
        return self.entries[0].location if self.entries else None


@dataclass
class Loop:
    """What makes a step a loop: a ``repeat()`` block with a title (ACT-10). The
    step's entries are one pass, as in a ``Repeat``."""

    count: Optional[int]
    """Passes, or ``None`` when they could not be told apart."""
    until: Optional[str] = None
    counter: Optional[str] = None
    inc: Union[int, float, None] = None
    t_last: Optional[float] = None
    """Time of the last action of the last pass."""


Entry = Union[Action, Check, Repeat, "Step"]


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
    note: Optional[str] = None
    """Why, or anything the reader should know, from ``step(..., note=...)``."""
    loop: Optional[Loop] = None
    """Set when the step is a titled ``repeat()`` block: its entries are one pass."""
    applied_in: Optional[str] = None
    """Set on a pre-condition this test did not apply itself (PRE-3): the id of the
    test in which a shared fixture applied it. Such a step has no entries; its
    status is the one the pre-condition got when it was applied."""
    origin: Optional["Step"] = field(default=None, repr=False, compare=False)
    """With ``applied_in``: the step recorded when the pre-condition was applied
    (in this process). Never serialised."""
    opened_in: Optional[Section] = field(default=None, repr=False, compare=False)
    """The section of the phase the step was opened in. For a pre-condition, the
    only phase whose late checks (an adapter's) may be its own. Never serialised."""
    within: Optional["Step"] = field(default=None, repr=False, compare=False)
    """For a pre-condition written inside a step (PRE-2): that step. The record
    lists the pre-condition at the top level; adapters that match checks by
    source lines need to know its lines are inside that step's. Never serialised."""

    @property
    def actions(self) -> list[Action]:
        return [e for e in self.entries if isinstance(e, Action)]

    @property
    def checks(self) -> list[Check]:
        return [e for e in self.entries if isinstance(e, Check)]

    @property
    def children(self) -> list["Step"]:
        """Child steps, also those inside a ``repeat()`` block."""
        return _steps_in(self.entries)


def _steps_in(entries: list[Entry]) -> list[Step]:
    out: list[Step] = []
    for e in entries:
        if isinstance(e, Step):
            out.append(e)
        elif isinstance(e, Repeat):
            out.extend(_steps_in(e.entries))
    return out


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
