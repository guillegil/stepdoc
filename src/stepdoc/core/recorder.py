"""Recording API: ``step()``, ``record_action()``, checks (brief §7.1, §7.2, §7.4)."""

from __future__ import annotations

import functools
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

    def __init__(self, name: str = "", *, symbolic: bool = True, dry_run: bool = False) -> None:
        self.name = name
        self.symbolic = symbolic
        self.dry_run = dry_run
        self.started = _perf()
        self.started_at = time.time()
        self.ended: Optional[float] = None
        self.steps: list[Step] = []
        self.unscoped: list[Union[Action, Check]] = []  # ACT-3
        self._stack: list[Step] = []
        self._tokens: list[Any] = []

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
            return str(len(self.steps) + 1)
        return f"{parent.number}.{len(parent.entries) + 1}"

    def _open(self, title: str, location: Optional[Location]) -> Step:
        parent = self._stack[-1] if self._stack else None
        s = Step(number=self._number_in(parent), title=title, started=_perf(), location=location)
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
                self._add_to(s, Check(text=text, passed=passed, detail=message))
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
            self.unscoped.append(entry)
            return
        entry.number = f"{s.number}.{len(s.entries) + 1}"
        s.entries.append(entry)

    def add_action(self, action: Action) -> None:
        self._add_to(self.current_step, action)

    def add_check(self, check: Check) -> None:
        if self.dry_run:
            _unjudge(check)
        self._add_to(self.current_step, check)

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
        for e in self.unscoped:
            if isinstance(e, Action):
                yield e

        def walk(steps: list[Step]) -> Iterator[Action]:
            for s in steps:
                for e in s.entries:
                    if isinstance(e, Action):
                        yield e
                    elif isinstance(e, Step):
                        yield from walk([e])

        yield from walk(self.steps)

    @property
    def status(self) -> Status:
        """Combined status of the recorded steps (not pytest's verdict)."""
        if self.dry_run:
            return "unknown"
        statuses = [s.status for s in self.steps]
        statuses += [check_status(c) for c in self.unscoped if isinstance(c, Check)]
        return combine(statuses) if statuses else "unknown"


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
    """

    def __init__(self, title: str, *, check: Any = _NO_CHECK) -> None:
        self.title = title
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
        return self._step

    def __exit__(
        self,
        et: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> bool:
        if self._rec is not None and self._step is not None:
            return self._rec._close(self._step, exc)
        return False

    def __call__(self, fn: F) -> F:
        if self._leaf:
            raise TypeError("step(..., check=...) cannot decorate a function")
        title = self.title

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with step(title):
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
    if rec is None:
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
    rec.add_action(a)
    return a
