"""Recording API: ``step()``, ``record_action()``, checks (brief §7.1, §7.2, §7.4)."""

from __future__ import annotations

import functools
import os
import time
from contextvars import ContextVar
from types import TracebackType
from typing import Any, Callable, Iterator, Optional, Sequence, TypeVar, Union

from .model import (
    DEFAULT_DIRECTIONS,
    Action,
    Check,
    Direction,
    Location,
    Section,
    Status,
    Step,
    check_status,
    combine,
)
from .redaction import _SCALARS, redact, redact_text
from .symbolic import assert_source, capture

F = TypeVar("F", bound=Callable[..., Any])

_current: ContextVar[Optional["Recorder"]] = ContextVar("stepdoc_recorder", default=None)
_perf = time.perf_counter


class Recorder:
    """Holds the step tree of one test (or one script run).

    ``with Recorder(): ...`` makes it the target of ``step()`` and
    ``record_action()`` in the current context. ``dry_run=True`` records checks
    without judging them and lets assertion failures inside steps pass so the
    rest of the procedure is still collected (DRY-1).
    """

    def __init__(
        self,
        name: str = "",
        *,
        symbolic: bool = True,
        dry_run: bool = False,
        collapse_repeats: bool = True,
    ) -> None:
        self.name = name
        self.symbolic = symbolic
        self.dry_run = dry_run
        self.collapse_repeats = collapse_repeats
        self.section: Section = "procedure"
        """Phase new top-level steps and unscoped entries belong to (STEP-8). The
        pytest plugin sets it around fixture setup and teardown."""
        self.started = _perf()
        self.started_at = time.time()
        self.ended: Optional[float] = None
        self.steps: list[Step] = []
        self.unscoped: list[Union[Action, Check]] = []  # ACT-3
        self._stack: list[Step] = []
        self._tokens: list[Any] = []
        self._quiet = 0
        self._names: list[tuple[str, Any]] = []

    # Activation ----------------------------------------------------------- #
    def __enter__(self) -> "Recorder":
        self._tokens.append(_current.set(self))
        return self

    def __exit__(self, *exc: Any) -> None:
        self.ended = _perf()
        _current.reset(self._tokens.pop())

    # Steps ---------------------------------------------------------------- #
    def _number_in(self, parent: Optional[Step]) -> str:
        if parent is None:
            n = sum(1 for s in self.steps if s.section == self.section) + 1
            return f"{_SECTION_PREFIX[self.section]}{n}"
        return f"{parent.number}.{len(parent.entries) + 1}"

    def _open(self, title: str, location: Optional[Location]) -> Step:
        parent = self._stack[-1] if self._stack else None
        section = self.section if parent is None else parent.section
        s = Step(
            number=self._number_in(parent),
            title=title,
            section=section,
            started=_perf(),
            location=location,
        )
        if parent is None:
            self.steps.append(s)
        else:
            parent.entries.append(s)
        self._stack.append(s)
        return s

    def _close(self, s: Step, exc: Optional[BaseException]) -> bool:
        """Close ``s``; returns True when ``exc`` must be swallowed (dry-run only)."""
        s.ended = _perf()
        if self._stack and self._stack[-1] is s:
            self._stack.pop()
        swallow = False
        own: list[Status] = []
        if exc is not None:
            if isinstance(exc, AssertionError):
                source = assert_source(exc.__traceback__)
                message = str(exc).strip() or None
                text = source or _assert_text(exc)
                passed: Optional[bool] = None if self.dry_run else False
                check = Check(text=text, passed=passed, detail=message, location=_tb_location(exc))
                self._attach(s, check)
                own.append("failed")
                swallow = self.dry_run
            else:
                own.append("error")
            s.error = f"{type(exc).__name__}: {exc}"
        statuses = own + [_entry_status(e) for e in s.entries if not isinstance(e, Action)]
        s.status = "unknown" if self.dry_run else combine(statuses)
        return swallow

    @property
    def current_step(self) -> Optional[Step]:
        return self._stack[-1] if self._stack else None

    # Entries -------------------------------------------------------------- #
    def _add_to(self, s: Optional[Step], entry: Union[Action, Check]) -> None:
        if s is None:
            entry.section = self.section
            self.unscoped.append(entry)
            return
        entry.number = f"{s.number}.{len(s.entries) + 1}"
        s.entries.append(entry)

    def _container(self, s: Optional[Step]) -> list[Any]:
        return self.unscoped if s is None else s.entries

    def add_action(self, action: Action) -> None:
        s = self.current_step
        if self.collapse_repeats:
            entries = self._container(s)
            if entries and _is_repeat(entries[-1], action):
                last: Action = entries[-1]
                last.count += 1
                last.t_last = action.t
                last.value = action.value
                last.meta = action.meta
                return
        self._add_to(s, action)

    def add_check(self, check: Check) -> None:
        self._attach(self.current_step, check)

    def _attach(self, s: Optional[Step], check: Check) -> None:
        """Add ``check`` to ``s``, absorbing the reads that fed it (ACT-5): result
        actions at the end of the step that ran on the check's source line."""
        if self.dry_run:
            _unjudge(check)
        loc = check.location
        if loc is not None:
            entries = self._container(s)
            reads: list[Action] = []
            while entries and isinstance(entries[-1], Action) and _feeds(entries[-1], loc):
                reads.append(entries.pop())
            for a in reads:
                a.number = None
                a.section = None
            check.reads = reads[::-1]
        self._add_to(s, check)

    def add_leaf(self, title: str, check: Check, location: Optional[Location]) -> Step:
        """STEP-3: a one-line step holding a single check."""
        s = self._open(title, location)
        s.ended = s.started
        if self.dry_run:
            _unjudge(check)
        self._add_to(s, check)
        self._stack.pop()
        s.status = "unknown" if self.dry_run else check_status(check)
        return s

    # Resolution ----------------------------------------------------------- #
    def resolve(self) -> None:
        """Resolve every captured site into symbolic text (call before the source
        files can change, e.g. at test teardown)."""
        for a in self.iter_actions():
            a.resolve()

    def iter_actions(self) -> Iterator[Action]:
        def walk(entries: Sequence[Any]) -> Iterator[Action]:
            for e in entries:
                if isinstance(e, Action):
                    yield e
                elif isinstance(e, Check):
                    yield from e.reads
                elif isinstance(e, Step):
                    yield from walk(e.entries)

        yield from walk(self.unscoped)
        yield from walk(self.steps)

    @property
    def status(self) -> Status:
        """Combined status of the recorded steps (not pytest's verdict)."""
        if self.dry_run:
            return "unknown"
        statuses = [s.status for s in self.steps]
        statuses += [check_status(c) for c in self.unscoped if isinstance(c, Check)]
        return combine(statuses) if statuses else "unknown"


_SECTION_PREFIX: dict[Section, str] = {"setup": "S", "procedure": "", "teardown": "T"}


def _is_repeat(last: Any, new: Action) -> bool:
    """ACT-4: the same result-producing event again, from the same code location
    (a polling loop), e.g. a status register or a job-status endpoint."""
    if not isinstance(last, Action) or new.direction == "in":
        return False
    if (last.op, last.target, last.direction) != (new.op, new.target, new.direction):
        return False
    if last.site is not None and new.site is not None:
        # Same line, not same instruction: a ``while`` condition compiles twice.
        if (last.site.code, last.site.lineno) != (new.site.code, new.site.lineno):
            return False
    if new.direction == "exchange":
        try:
            return bool(last.value == new.value)
        except Exception:
            return False
    return True


def _action_line(a: Action) -> Optional[tuple[str, int]]:
    if a.site is not None:
        return a.site.filename, a.site.lineno
    if a.location is not None:
        return a.location.file, a.location.line
    return None


def _feeds(a: Action, loc: Location) -> bool:
    if a.direction != "out":
        return False
    where = _action_line(a)
    return where is not None and where[1] == loc.line and _same_file(where[0], loc.file)


def _same_file(a: str, b: str) -> bool:
    if a == b:
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _tb_location(exc: BaseException) -> Optional[Location]:
    tb = exc.__traceback__
    if tb is None:
        return None
    while tb.tb_next is not None:
        tb = tb.tb_next
    return Location(tb.tb_frame.f_code.co_filename, tb.tb_lineno)


def _entry_status(e: Union[Check, Step]) -> Status:
    return check_status(e) if isinstance(e, Check) else e.status


def _unjudge(c: Check) -> None:
    c.passed = None
    for child in c.children:
        _unjudge(child)


def _assert_text(exc: AssertionError) -> str:
    msg = str(exc).strip()
    return msg.splitlines()[0] if msg else "assertion failed"


def current_recorder() -> Optional[Recorder]:
    return _current.get()


def current_step() -> Optional[Step]:
    rec = _current.get()
    return rec.current_step if rec is not None else None


def is_dry_run(config: Any = None) -> bool:
    """True in dry-run. With a pytest ``config``, reads ``--stepdoc-dry-run``;
    without one, asks the active recorder."""
    if config is not None:
        return bool(getattr(getattr(config, "option", None), "stepdoc_dry_run", False))
    rec = _current.get()
    return rec is not None and rec.dry_run


# --------------------------------------------------------------------------- #
# Checks (CHK-1…3, 7, 8). stepdoc never compares anything itself.
# --------------------------------------------------------------------------- #

Converter = Callable[[Any], Check]
_converters: list[tuple[Callable[[Any], bool], Converter]] = []


def register_check_converter(predicate: Callable[[Any], bool], fn: Converter) -> None:
    """Teach stepdoc to turn another tool's check object into a ``Check`` (CHK-2)."""
    _converters.append((predicate, fn))


class CheckTypeError(TypeError):
    """Raised for a ``check=`` value no converter understands (CHK-3)."""


def to_check(obj: Any, text: str) -> Check:
    if isinstance(obj, Check):
        return obj
    if isinstance(obj, bool):
        return Check(text=text, passed=obj)
    for predicate, fn in _converters:
        if predicate(obj):
            return fn(obj)
    if callable(obj):
        result = obj()
        if isinstance(result, bool):
            return Check(text=text, passed=result)
        raise CheckTypeError(
            f"check callable returned {type(result).__name__}, expected bool"
        )
    module = type(obj).__module__.split(".")[0]
    hint = (
        f" It looks like it comes from '{module}': enable stepdoc's adapter for it."
        if module not in ("builtins", "__main__")
        else ""
    )
    raise CheckTypeError(
        f"stepdoc cannot use a check of type {type(obj).__module__}.{type(obj).__qualname__}."
        f" Pass a bool, a callable returning bool, or stepdoc.Check.{hint}"
    )


def attach_check(check: Union[Check, bool], text: str = "") -> Optional[Check]:
    """Add a check to the current step (or the unscoped section)."""
    rec = _current.get()
    if rec is None:
        return None
    c = to_check(check, text)
    rec.add_check(c)
    return c


# --------------------------------------------------------------------------- #
# step()
# --------------------------------------------------------------------------- #

_NO_CHECK = object()


class step:  # noqa: N801 - public API name from the brief
    """``with step("title"):`` · ``@step("title")`` · ``step("title", check=...)``.

    Nesting in the code decides the numbering (STEP-1). Exceptions are attached
    to the step and re-raised (STEP-5); in dry-run, assertion failures inside a
    step are recorded and swallowed so later steps are still documented.

    ``record=False`` (STEP-7) keeps the step but drops the actions recorded inside
    it, including in nested steps: a helper then reads as one line. Checks and
    nested steps are still recorded.
    """

    def __init__(self, title: str, *, check: Any = _NO_CHECK, record: bool = True) -> None:
        self.title = title
        self.record = record
        self._step: Optional[Step] = None
        self._rec: Optional[Recorder] = None
        self._leaf = check is not _NO_CHECK
        if self._leaf:
            rec = _current.get()
            c = to_check(check, title)
            if rec is not None:
                c.location = _caller_location()
                rec.add_leaf(title, c, c.location)

    def __enter__(self) -> Optional[Step]:
        if self._leaf:
            raise TypeError("step(..., check=...) records a one-line step; it is not a context manager")
        self._rec = _current.get()
        if self._rec is not None:
            self._step = self._rec._open(self.title, _caller_location())
            if not self.record:
                self._rec._quiet += 1
        return self._step

    def __exit__(
        self,
        et: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> bool:
        if self._rec is not None and self._step is not None:
            if not self.record:
                self._rec._quiet -= 1
            return self._rec._close(self._step, exc)
        return False

    def __call__(self, fn: F) -> F:
        if self._leaf:
            raise TypeError("step(..., check=...) cannot decorate a function")
        title, record = self.title, self.record

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with step(title, record=record):
                return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]


def _caller_location() -> Optional[Location]:
    site = capture(0)
    return None if site is None else Location(site.filename, site.lineno)


# --------------------------------------------------------------------------- #
# record_action()
# --------------------------------------------------------------------------- #


def record_action(
    target: str,
    op: str,
    value: Any = None,
    *,
    direction: Optional[Direction] = None,
    value_from: Optional[Sequence[Union[str, int]]] = None,
    target_from: Sequence[str] = (),
    bridge_frames: int = 1,
    **meta: Any,
) -> Optional[Action]:
    """Record an action on the system under test (ACT-1). Called by bridges.

    ``direction``: ``"in"`` (the test sends ``value``), ``"out"`` (``value`` is a
    result, e.g. a read or a measurement) or ``"exchange"`` (a request whose
    response goes in ``meta``). Defaults from ``op`` for common names, else ``"in"``.

    ``value_from`` names the call argument that carries the value; names are bound
    to positions through the called function's signature. ``target`` may be a
    ``str.format`` template: its ``target_from`` fields are filled concretely from
    ``meta`` and symbolically from the call argument of the same name.
    ``bridge_frames`` is how many frames above this call belong to the bridge.

    Values, ``meta`` and the target are redacted before they are stored (ACT-9).
    """
    rec = _current.get()
    if rec is None or rec._quiet:
        return None
    template = None
    if target_from:
        template = target
        target = target.format(**meta)
    a = Action(
        target=redact_text(target),
        op=op,
        value=value if isinstance(value, _SCALARS) else redact(value),
        t=_perf(),
        direction=direction or DEFAULT_DIRECTIONS.get(op, "in"),
        meta=redact(meta) if meta else meta,
    )
    if rec.symbolic:
        a.site = capture(bridge_frames)
        a.value_from = value_from
        a.target_fields = tuple(target_from)
        a.target_template = template
    if rec._names and a.direction != "out":
        a.name = _take_name(rec, value)
    rec.add_action(a)
    return a


# --------------------------------------------------------------------------- #
# value() (SYM-4)
# --------------------------------------------------------------------------- #

V = TypeVar("V")
_MAX_NAMES = 16


def value(name: str, v: V) -> V:
    """Give ``v`` a symbolic name and return it unchanged (SYM-4)::

        dev.map.dac.level = stepdoc.value("code", compute_code(vref))

    The procedure shows ``<code>``. Written inline like this, the name comes from
    the source. When the source is not available (or the value travels through
    other code first), the next action that sends an equal value takes the name.
    """
    rec = _current.get()
    if rec is not None:
        names = rec._names
        names.append((name, v))
        if len(names) > _MAX_NAMES:
            del names[0]
    return v


def _take_name(rec: Recorder, sent: Any) -> Optional[str]:
    names = rec._names
    for i in range(len(names) - 1, -1, -1):
        name, v = names[i]
        try:
            same = v is sent or bool(v == sent)
        except Exception:
            same = False
        if same:
            del names[i]
            return name
    return None
