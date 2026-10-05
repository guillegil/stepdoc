"""Recording API: ``step()`` and ``record_action()`` (minimal subset for the spike)."""

from __future__ import annotations

import functools
import time
from contextvars import ContextVar
from types import TracebackType
from typing import Any, Callable, Optional, Sequence, TypeVar, Union

from .model import Action, Step
from .symbolic import capture

F = TypeVar("F", bound=Callable[..., Any])

_current: ContextVar[Optional["Recorder"]] = ContextVar("stepdoc_recorder", default=None)
_perf = time.perf_counter


class Recorder:
    """Holds the step tree of one test (or one script run)."""

    def __init__(self, *, symbolic: bool = True) -> None:
        self.symbolic = symbolic
        self.steps: list[Step] = []
        self.unscoped: list[Action] = []  # ACT-3
        self._stack: list[Step] = []
        self._token: Any = None

    # Activation ---------------------------------------------------------- #
    def __enter__(self) -> "Recorder":
        self._token = _current.set(self)
        return self

    def __exit__(self, *exc: Any) -> None:
        _current.reset(self._token)

    # Steps ---------------------------------------------------------------- #
    def _open(self, title: str) -> Step:
        siblings = self._stack[-1].children if self._stack else self.steps
        parent = self._stack[-1].number + "." if self._stack else ""
        s = Step(number=f"{parent}{len(siblings) + 1}", title=title, started=_perf())
        siblings.append(s)
        self._stack.append(s)
        return s

    def _close(self, s: Step, exc: Optional[BaseException]) -> None:
        s.ended = _perf()
        self._stack.pop()
        if exc is not None:
            s.status = "failed" if isinstance(exc, AssertionError) else "error"
            s.error = f"{type(exc).__name__}: {exc}"
        elif any(c.status in ("failed", "error") for c in s.children):
            s.status = "failed"
        else:
            s.status = "passed"

    # Actions -------------------------------------------------------------- #
    def add_action(self, action: Action) -> None:
        (self._stack[-1].actions if self._stack else self.unscoped).append(action)

    def resolve(self) -> None:
        """Resolve every captured site into symbolic text."""
        for a in self.iter_actions():
            a.resolve()

    def iter_actions(self) -> Any:
        yield from self.unscoped

        def walk(steps: list[Step]) -> Any:
            for s in steps:
                yield from s.actions
                yield from walk(s.children)

        yield from walk(self.steps)


def current_recorder() -> Optional[Recorder]:
    return _current.get()


class step:  # noqa: N801 - public API name from the brief
    """``with step("title"):`` or ``@step("title")``."""

    def __init__(self, title: str) -> None:
        self.title = title
        self._step: Optional[Step] = None
        self._rec: Optional[Recorder] = None

    def __enter__(self) -> Optional[Step]:
        self._rec = _current.get()
        if self._rec is not None:
            self._step = self._rec._open(self.title)
        return self._step

    def __exit__(
        self,
        et: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        if self._rec is not None and self._step is not None:
            self._rec._close(self._step, exc)
        # never swallow (STEP-5)

    def __call__(self, fn: F) -> F:
        title = self.title

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with step(title):
                return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]


def record_action(
    target: str,
    op: str,
    value: Any = None,
    *,
    value_from: Optional[Sequence[Union[str, int]]] = None,
    target_from: Sequence[str] = (),
    bridge_frames: int = 1,
    **meta: Any,
) -> Optional[Action]:
    """Record an action on the system under test (ACT-1).

    ``value_from`` picks which call argument carries the value (names are bound
    to positions through the called function's signature). ``target`` may be a
    ``str.format`` template whose ``target_from`` fields are filled concretely
    from ``meta`` and symbolically from the call argument of the same name.
    ``bridge_frames`` is how many frames above this call belong to the bridge.
    """
    rec = _current.get()
    if rec is None:
        return None
    template = None
    if target_from:
        template = target
        target = target.format(**meta)
    a = Action(target=target, op=op, value=value, t=_perf(), meta=meta)
    if rec.symbolic:
        a.site = capture(bridge_frames)
        a.value_from = value_from
        a.target_fields = tuple(target_from)
        a.target_template = template
    rec.add_action(a)
    return a
