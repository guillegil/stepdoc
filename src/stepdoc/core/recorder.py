"""Recording API: ``step()``, ``record_action()``, checks (brief §7.1, §7.2, §7.4)."""

from __future__ import annotations

import dis
import functools
import inspect
import os
import sys
import time
from contextvars import ContextVar
from types import TracebackType
from typing import Any, Callable, Collection, Iterator, Optional, Sequence, TypeVar, Union

from .model import (
    DEFAULT_DIRECTIONS,
    Action,
    Check,
    Direction,
    Location,
    Loop,
    Repeat,
    Section,
    Status,
    Step,
    check_status,
    combine,
)
from .redaction import _SCALARS, redact, redact_text
from .comments import comment_note
from .symbolic import assert_source, capture

F = TypeVar("F", bound=Callable[..., Any])
Listener = Callable[[str, Step], None]
"""Called with ``"started"`` or ``"finished"`` and the step (EXT-1)."""

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
        comment_notes: bool = True,
    ) -> None:
        self.name = name
        self.symbolic = symbolic
        self.dry_run = dry_run
        self.collapse_repeats = collapse_repeats
        self.comment_notes = comment_notes
        """Take a check's note from the source comment at its line when it has none
        (CHK-9); looked up in :meth:`resolve`, after the test."""
        self.params: Optional[frozenset[str]] = None
        """The names of the test's parameters (the pytest plugin sets them from
        ``callspec.params``): only values that come from them stay generic in the
        procedure (SYM-9). ``None``: not known, only named constants show their
        value."""
        self.section: Section = "procedure"
        """Phase new top-level steps and unscoped entries belong to (STEP-8). The
        pytest plugin sets it around fixture setup and teardown."""
        self.started = _perf()
        self.started_at = time.time()
        self.ended: Optional[float] = None
        self.steps: list[Step] = []
        self.unscoped: list[Union[Action, Check, Repeat]] = []  # ACT-3
        self._stack: list[Step] = []
        self._tokens: list[Any] = []
        self._quiet = 0
        self._names: list[tuple[str, Any]] = []
        self._floors: dict[int, int] = {}
        """id(container) -> index before which ``repeat()`` blocks collapsing."""
        self.listeners: list[Listener] = []

    # Activation ----------------------------------------------------------- #
    def __enter__(self) -> "Recorder":
        self._tokens.append(_current.set(self))
        return self

    def __exit__(self, *exc: Any) -> None:
        self._flush(None)
        self.ended = _perf()
        _current.reset(self._tokens.pop())

    # Steps ---------------------------------------------------------------- #
    def _number_in(self, parent: Optional[Step], section: Section) -> str:
        if parent is None:
            n = sum(1 for s in self.steps if s.section == section) + 1
            return f"{_SECTION_PREFIX[section]}{n}"
        return f"{parent.number}.{len(parent.entries) + 1}"

    def _open(
        self,
        title: str,
        location: Optional[Location],
        note: Optional[str] = None,
        *,
        precondition: bool = False,
    ) -> Step:
        parent = self._stack[-1] if self._stack else None
        within: Optional[Step] = None
        self._flush(parent)
        if precondition and (parent is None or parent.section != "precondition"):
            # PRE-2: a pre-condition is never part of a setup or procedure step. It
            # opens at the top of its own section; the step it was written in
            # goes on after it. What that step does next is not a repeat of what
            # it did before the pre-condition.
            around = self._container(parent)
            self._floors[id(around)] = len(around)
            within, parent = parent, None
        if precondition:
            section: Section = "precondition"
        else:
            section = self.section if parent is None else parent.section
        s = Step(
            number=self._number_in(parent, section),
            title=title,
            section=section,
            started=_perf(),
            location=location,
            note=note,
            opened_in=self.section,
            within=within,
        )
        if parent is None:
            self.steps.append(s)
        else:
            parent.entries.append(s)
        self._stack.append(s)
        self._notify("started", s)
        return s

    def _notify(self, event: str, s: Step) -> None:
        for listener in self.listeners:
            listener(event, s)

    def _close(self, s: Step, exc: Optional[BaseException]) -> bool:
        """Close ``s``; returns True when ``exc`` must be swallowed (dry-run only)."""
        self._flush(s)
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
                check.note_from_comment = True
                self._attach(s, check)
                own.append("failed")
                swallow = self.dry_run
            else:
                own.append("error")
            s.error = f"{type(exc).__name__}: {exc}"
        statuses = own + [_entry_status(e) for e in s.entries if not isinstance(e, Action)]
        s.status = "unknown" if self.dry_run else combine(statuses)
        self._notify("finished", s)
        return swallow

    @property
    def current_step(self) -> Optional[Step]:
        return self._stack[-1] if self._stack else None

    # Entries -------------------------------------------------------------- #
    def _add_to(self, s: Optional[Step], entry: Union[Action, Check, Repeat]) -> None:
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
        if not self.collapse_repeats:
            self._add_to(s, action)
            return
        entries = self._container(s)
        floor = self._floors.get(id(entries), 0)
        if len(entries) > floor:
            last = entries[-1]
            if isinstance(last, Repeat) and last.partial is not None:
                if _continue(last, action):
                    return
                self._flush(s)
                last = entries[-1]
            if _is_repeat(last, action):  # ACT-4: polling, or the same write again
                _absorb(last, action)
                return
        self._add_to(s, action)
        self._fold(s, entries, floor)

    def _fold(self, s: Optional[Step], entries: list[Any], floor: int) -> None:
        """ACT-10: when the last ``2p`` entries are the same ``p`` actions twice, they
        become a ``Repeat`` that later passes of the loop add to."""
        n = len(entries)
        new = entries[-1]
        for p in range(2, _MAX_PASS + 1):
            if n - 2 * p < floor:
                return
            if not _same_event(entries[n - 1 - p], new):
                continue
            first, second = entries[n - 2 * p : n - p], entries[n - p :]
            if all(map(_same_event, first, second)):
                for a, b in zip(first, second):
                    _take_values(a, b)
                rep = Repeat(entries=first, count=2, t=_entry_time(first[0]), t_last=_end_time(second[-1]), partial=[])
                del entries[n - 2 * p :]
                self._add_to(s, rep)
                _renumber(rep)
                return

    def _flush(self, s: Optional[Step]) -> None:
        """Close the loop being collapsed at the end of ``s``: the actions of an
        unfinished pass become ordinary entries after it."""
        entries = self._container(s)
        if not entries:
            return
        last = entries[-1]
        if isinstance(last, Repeat) and last.partial is not None:
            partial, last.partial = last.partial, None
            for e in partial:
                if isinstance(e, Check):  # its reads are the actions just added
                    del entries[len(entries) - len(e.reads) :]
                self._add_to(s, e)

    def _close_repeat(
        self,
        s: Optional[Step],
        start: int,
        until: Optional[str],
        clean: bool,
    ) -> None:
        """End of a ``repeat()`` block whose entries start at ``start``."""
        self._flush(s)
        entries = self._container(s)
        region = entries[start:]
        rest: list[Any] = []
        if not region:
            rep = Repeat(entries=[], count=0, t=_perf(), until=until)
        else:
            head, rest = region[0], region[1:]
            if isinstance(head, Repeat) and head.until is None:
                rep = head
            elif isinstance(head, Action) and (head.count > 1 or not rest):
                rep = Repeat(entries=[head], count=head.count, t=head.t, t_last=head.t_last)
                head.count, head.t_last = 1, None
            else:
                rep, rest = Repeat(entries=region, count=None, t=_entry_time(head)), []
            rep.until = until
            if clean and rep.count is not None and _is_prefix(rest, rep.entries):
                # The last test of a ``while`` condition: part of the loop, not a pass.
                for a, b in zip(rep.entries, rest):
                    _take_values(a, b)
                rest = []
        entries[start:] = [rep, *rest]
        if s is None:
            rep.section = self.section
        else:
            _renumber(s)

    def add_check(self, check: Check) -> None:
        self._attach(self.current_step, check)

    def _attach(self, s: Optional[Step], check: Check) -> None:
        """Add ``check`` to ``s``, absorbing the reads that fed it (ACT-5): result
        actions at the end of the step that ran on the check's source line."""
        if self.dry_run:
            _unjudge(check)
        entries = self._container(s)
        loc = check.location
        floor = self._floors.get(id(entries), 0)
        last = entries[-1] if len(entries) > floor else None
        if isinstance(last, Repeat) and last.partial is not None:
            # A check made in every pass of a loop (a passing assert) is part of it.
            partial = last.partial
            n = 0
            if loc is not None:
                while (
                    n < len(partial)
                    and isinstance(partial[-1 - n], Action)
                    and _feeds(partial[-1 - n], loc)
                ):
                    n += 1
            check.reads = list(partial[len(partial) - n :])
            if _continue(last, check):
                return
            check.reads = []
            self._flush(s)
        if loc is not None:
            reads: list[Action] = []
            while entries and isinstance(entries[-1], Action) and _feeds(entries[-1], loc):
                reads.append(entries.pop())
            for a in reads:
                a.number = None
                a.section = None
            check.reads = reads[::-1]
        self._add_to(s, check)
        if self.collapse_repeats:
            self._fold(s, entries, floor)

    def add_leaf(
        self, title: str, check: Check, location: Optional[Location], note: Optional[str] = None
    ) -> Step:
        """STEP-3: a one-line step holding a single check."""
        s = self._open(title, location, note)
        s.ended = s.started
        if self.dry_run:
            _unjudge(check)
        self._add_to(s, check)
        self._stack.pop()
        s.status = "unknown" if self.dry_run else check_status(check)
        return s

    def insert_check(
        self,
        path: Sequence[Step],
        check: Check,
        index: Optional[int] = None,
        *,
        fold: Optional[tuple[int, int]] = None,
        start: int = 0,
        fold_exprs: Optional[Collection[str]] = None,
    ) -> int:
        """Add ``check`` to a step that may have finished already, for checks that
        arrive after the fact (an adapter reading another tool's results). ``path``
        runs from a top-level step down to the target step; an empty path means the
        unscoped section. The check goes at ``index`` among the step's entries (the
        end by default), entries are renumbered, and the statuses along ``path``
        take the check's result into account.

        ``fold`` (first and last source line of the call that made the check)
        absorbs the reads that fed it, as for an ``assert`` (ACT-5): the first run
        of results recorded on those lines of the check's file, between ``start``
        and ``index``, moves into ``check.reads`` and the check takes its place. A
        run holds each read expression once, so in a loop each pass's check takes
        its own pass's reads. With ``fold_exprs`` (the expressions in the call),
        a read whose source expression is known must be one of them, so two
        checks on one line take their own reads. Returns the index the check ends
        up at."""
        if self.dry_run:
            _unjudge(check)
        if not path:
            self._flush(None)
            check.section = check.section or self.section
            entries: list[Any] = self.unscoped
        else:
            entries = path[-1].entries
        at = len(entries) if index is None else min(index, len(entries))
        if fold is not None and check.location is not None:
            at = _fold_reads(entries, max(start, 0), at, check, fold, fold_exprs)
        entries.insert(at, check)
        if not path:
            return at
        _renumber(path[-1], at)
        if not self.dry_run:
            for s in path:
                s.status = combine([s.status, check_status(check)])
        return at

    def mark_unknown(self) -> None:
        """NFR-7: a judge stepdoc cannot see ran in this test, so no step can be
        shown as passed. Failed and errored steps keep their status."""

        def walk(steps: Sequence[Union[Step, Repeat]]) -> None:
            for s in steps:
                if isinstance(s, Repeat):
                    walk([e for e in s.entries if isinstance(e, (Step, Repeat))])
                    continue
                if s.status == "passed":
                    s.status = "unknown"
                walk([e for e in s.entries if isinstance(e, (Step, Repeat))])

        walk(self.steps)

    # Resolution ----------------------------------------------------------- #
    def resolve(self) -> None:
        """Resolve every captured site into symbolic text (call before the source
        files can change, e.g. at test teardown)."""
        self._flush(None)
        for a in self.iter_actions():
            a.resolve(self.params)
        self.resolve_notes()

    def resolve_notes(self) -> None:
        """Give checks waiting for one the note of the source comment at their line
        (CHK-9). Cheap and idempotent: comments are read once per file and line.
        The pytest plugin calls it at the end of each test phase, so tools that
        read the steps when the test ends (pytest-reporter) see the notes."""
        if not self.comment_notes:
            return
        for c in self.iter_checks():
            if c.note_from_comment:
                c.note_from_comment = False
                if c.note is None and c.location is not None:
                    c.note = comment_note(c.location.file, c.location.line)

    def iter_checks(self) -> Iterator[Check]:
        """Every check, in steps, repeats and the unscoped section (not children)."""

        def walk(entries: Sequence[Any]) -> Iterator[Check]:
            for e in entries:
                if isinstance(e, Check):
                    yield e
                elif isinstance(e, (Step, Repeat)):
                    yield from walk(e.entries)

        yield from walk(self.unscoped)
        yield from walk(self.steps)

    def iter_actions(self) -> Iterator[Action]:
        def walk(entries: Sequence[Any]) -> Iterator[Action]:
            for e in entries:
                if isinstance(e, Action):
                    yield e
                elif isinstance(e, Check):
                    yield from e.reads
                elif isinstance(e, (Step, Repeat)):
                    yield from walk(e.entries)

        yield from walk(self.unscoped)
        yield from walk(self.steps)

    @property
    def status(self) -> Status:
        """Combined status of the recorded steps (not pytest's verdict)."""
        if self.dry_run:
            return "unknown"
        statuses = [s.status for s in self.steps]
        statuses += [_entry_status(e) for e in self.unscoped if not isinstance(e, Action)]
        return combine(statuses) if statuses else "unknown"


_SECTION_PREFIX: dict[Section, str] = {"precondition": "P", "setup": "S", "procedure": "", "teardown": "T"}


_MAX_PASS = 8
"""Longest loop body, in actions, that collapses on its own (ACT-10)."""


def _same_event(a: Any, b: Any) -> bool:
    """The same action from the same code: same op and target, and for writes and
    requests the same value (reads may return anything). For checks: the same
    check from the same line, passed (or not judged) both times."""
    if type(a) is Check and type(b) is Check:
        return (
            a.text == b.text
            and a.location == b.location
            and a.passed is not False
            and a.passed == b.passed
            and not a.children
            and not b.children
            and len(a.reads) == len(b.reads)
            and all(map(_same_event, a.reads, b.reads))
        )
    if type(a) is not Action or type(b) is not Action:
        return False
    if (a.op, a.target, a.direction, a.name) != (b.op, b.target, b.direction, b.name):
        return False
    if a.site is not None and b.site is not None:
        # Same line, not same instruction: a ``while`` condition compiles twice.
        if (a.site.code, a.site.lineno) != (b.site.code, b.site.lineno):
            return False
    if b.direction != "out":
        try:
            return bool(a.value == b.value)
        except Exception:
            return False
    return True


def _is_repeat(last: Any, new: Action) -> bool:
    """ACT-4: the same event again, from the same code location: a polling loop
    (a status register, a job-status endpoint) or the same write in a loop."""
    return _same_event(last, new)


def _absorb(last: Action, new: Action) -> None:
    last.count += 1
    last.t_last = new.t
    _take_values(last, new)


def _take_values(kept: Any, new: Any) -> None:
    """A collapsed entry shows the values of the latest event."""
    if isinstance(kept, Check):
        kept.actual, kept.expected, kept.detail = new.actual, new.expected, new.detail
        for a, b in zip(kept.reads, new.reads):
            _take_values(a, b)
        return
    kept.value = new.value
    kept.meta = new.meta


def _flat(entries: Sequence[Any]) -> list[Any]:
    """One pass as it is recorded: each check after the reads that fed it."""
    out: list[Any] = []
    for e in entries:
        if isinstance(e, Check):
            out.extend(e.reads)
        out.append(e)
    return out


def _continue(rep: Repeat, new: Union[Action, Check]) -> bool:
    """Add ``new`` to the pass in progress of ``rep`` if it is what the loop body
    does next (ACT-10); polling inside a pass stays one entry."""
    partial = rep.partial
    assert partial is not None
    if partial and isinstance(new, Action) and _same_event(partial[-1], new):
        _absorb(partial[-1], new)
        return True
    body: list[Any] = _flat(rep.entries)
    pos = len(partial)
    if pos < len(body) and _same_event(body[pos], new):
        partial.append(new)
        if len(partial) == len(body):
            for kept, new_value in zip(body, partial):
                if isinstance(kept, Check):  # its reads are separate items of the pass
                    kept.actual, kept.expected = new_value.actual, new_value.expected
                    kept.detail = new_value.detail
                else:
                    _take_values(kept, new_value)
            rep.count = (rep.count or 0) + 1
            rep.t_last = _end_time(new)
            rep.partial = []
        return True
    return False


def _is_prefix(rest: Sequence[Any], body: Sequence[Any]) -> bool:
    return 0 < len(rest) < len(body) and all(map(_same_event, body, rest))


def _entry_time(e: Any) -> float:
    if isinstance(e, Step):
        return e.started
    if isinstance(e, Check):
        return e.reads[0].t if e.reads else _perf()
    return float(e.t)


def _end_time(e: Any) -> float:
    if isinstance(e, Action):
        return e.t_last if e.t_last is not None else e.t
    return _perf()


def _action_line(a: Action) -> Optional[tuple[str, int]]:
    if a.site is not None:
        return a.site.filename, a.site.lineno
    if a.location is not None:
        return a.location.file, a.location.line
    return None


def _fold_reads(
    entries: list[Any],
    start: int,
    end: int,
    check: Check,
    fold: tuple[int, int],
    exprs: Optional[Collection[str]] = None,
) -> int:
    """Move the first run of results in ``entries[start:end]`` made on the lines
    ``fold`` of the check's file into ``check.reads``; returns where the check
    goes: where the run was, else ``end``."""
    loc = check.location
    assert loc is not None

    def feeds(e: Any) -> bool:
        if not isinstance(e, Action) or e.direction != "out":
            return False
        if exprs is not None and e.read_expr is not None and e.read_expr not in exprs:
            return False
        where = _action_line(e)
        return where is not None and fold[0] <= where[1] <= fold[1] and _same_file(where[0], loc.file)

    first = next((i for i in range(start, end) if feeds(entries[i])), None)
    if first is None:
        return end
    last = first
    seen: set[str] = set()
    while last < end and feeds(entries[last]):
        key = entries[last].read_expr or entries[last].target
        if key in seen:  # the next pass of a loop
            break
        seen.add(key)
        last += 1
    reads = entries[first:last]
    del entries[first:last]
    for a in reads:
        a.number = None
        a.section = None
    check.reads = [*reads, *check.reads]
    return first


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


def _renumber(s: Union[Step, Repeat], start: int = 0) -> None:
    """Number the entries of ``s`` from ``entries[start]`` on (and their children)."""
    for i in range(max(start, 0), len(s.entries)):
        e = s.entries[i]
        e.number = None if s.number is None else f"{s.number}.{i + 1}"
        if isinstance(e, (Step, Repeat)):
            _renumber(e)


def _entry_status(e: Union[Check, Repeat, Step]) -> Status:
    if isinstance(e, Check):
        return check_status(e)
    if isinstance(e, Repeat):
        return combine([_entry_status(x) for x in e.entries if not isinstance(x, Action)])
    return e.status


def _unjudge(c: Check) -> None:
    c.passed = None
    for child in c.children:
        _unjudge(child)
    for branch in c.branches or ():
        if branch.check is not None:
            _unjudge(branch.check)


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
    """``with step("title"):`` · ``@step("title")`` · ``step("title", check=...)``
    · ``step("title")``.

    Nesting in the code decides the numbering (STEP-1). Exceptions are attached
    to the step and re-raised (STEP-5); in dry-run, assertion failures inside a
    step are recorded and swallowed so later steps are still documented.

    ``record=False`` (STEP-7) keeps the step but drops the actions recorded inside
    it, including in nested steps: a helper then reads as one line. Checks and
    nested steps are still recorded.

    A bare ``step("title")`` call, a statement on its own, records a one-line step
    with no check (STEP-9): ``1.2`` inside an open step, a top-level step outside
    one. Event listeners use it to log what the system under test did.

    ``note=`` is a sentence for the reader (why the step is there, what a value
    means), shown under the step's line in the procedure and the report.
    """

    def __init__(
        self,
        title: str,
        *,
        check: Any = _NO_CHECK,
        record: bool = True,
        note: Optional[str] = None,
    ) -> None:
        self.title = title
        self.record = record
        self.note = _note(note)
        self._step: Optional[Step] = None
        self._rec: Optional[Recorder] = None
        self._leaf = check is not _NO_CHECK
        if self._leaf:
            rec = _current.get()
            c = to_check(check, title)
            if rec is not None:
                c.location = _caller_location()
                rec.add_leaf(title, c, c.location, self.note)
        elif _is_statement(sys._getframe(1)):
            rec = _current.get()
            if rec is not None:
                rec._close(rec._open(title, _caller_location(), self.note), None)

    def __enter__(self) -> Optional[Step]:
        if self._leaf:
            raise TypeError("step(..., check=...) records a one-line step; it is not a context manager")
        self._rec = _current.get()
        if self._rec is not None:
            self._step = self._rec._open(self.title, _caller_location(), self.note)
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
        title, record, note = self.title, self.record, self.note

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with step(title, record=record, note=note):
                return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]


class precondition:  # noqa: N801 - reads like ``step``
    """``with precondition("System Mode A"):`` · ``@precondition("System Mode A")``
    (PRE-1).

    A pre-condition is a named state the system must be in before the test, such
    as an operating mode that many tests share. What is recorded inside the block
    (steps, actions, checks) belongs to it and goes into the **Pre-conditions**
    section, apart from the rest of the setup: the block is ``P1``, its entries
    ``P1.1``, ``P1.2``… Like a step it gets a status, and exceptions are attached
    to it and re-raised.

    The section does not depend on where the block runs. In a fixture, the code
    around the block stays in Setup; a block written inside a step is not part
    of that step (PRE-2). Only a block inside another pre-condition is nested in
    it (``P1.1``).

    As a decorator it wraps the whole function. On a fixture that yields, the
    pre-condition is the part before the ``yield``; what runs after it is
    ordinary teardown::

        @pytest.fixture(scope="session")
        @stepdoc.precondition("System Mode A")
        def mode_a(dev1, dev2):
            ...
            yield
            ...

    Under pytest, a test that uses a fixture which applied a pre-condition for an
    earlier test (session, module or class scope) lists it too, as already
    established (PRE-3).

    ``note=`` is a sentence for the reader, shown under the line, as on ``step``.
    """

    def __init__(self, title: str, *, note: Optional[str] = None) -> None:
        if not isinstance(title, str):
            raise TypeError(f"a pre-condition's title must be a str, not {type(title).__name__}")
        self.title = title
        self.note = _note(note)
        self._blocks: list[tuple[Recorder, Step, int]] = []

    def __enter__(self) -> Optional[Step]:
        return self._enter(_caller_location())

    def _enter(self, location: Optional[Location]) -> Optional[Step]:
        rec = _current.get()
        if rec is None:
            return None
        s = rec._open(self.title, location, self.note, precondition=True)
        self._blocks.append((rec, s, rec._quiet))
        if "." not in s.number:
            # PRE-2: not part of the step it was written in, so that step's
            # record=False does not reach it.
            rec._quiet = 0
        return s

    def __exit__(
        self,
        et: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> bool:
        if not self._blocks:
            return False
        rec, s, quiet = self._blocks.pop()
        rec._quiet = quiet
        return rec._close(s, exc)

    def __call__(self, fn: F) -> F:
        # pytest 8.4+ returns a fixture definition object; older versions mark the function.
        if hasattr(fn, "_fixture_function_marker") or hasattr(fn, "_pytestfixturefunction"):
            raise TypeError(
                f"@stepdoc.precondition({self.title!r}) is above @pytest.fixture: "
                "put @pytest.fixture above @stepdoc.precondition(...)"
            )
        title, note = self.title, self.note
        code = getattr(fn, "__code__", None)
        where = None if code is None else Location(code.co_filename, code.co_firstlineno)

        def block() -> "precondition":
            return precondition(title, note=note)

        if inspect.isasyncgenfunction(fn):

            @functools.wraps(fn)
            async def agen_wrapper(*args: Any, **kwargs: Any) -> Any:
                gen = fn(*args, **kwargs)
                b = block()
                b._enter(where)
                try:
                    first = await gen.__anext__()
                except StopAsyncIteration:
                    b.__exit__(None, None, None)
                    return
                except BaseException as exc:
                    if not b.__exit__(type(exc), exc, exc.__traceback__):
                        raise
                    yield None  # dry-run: the failure is recorded, the test goes on
                    return
                b.__exit__(None, None, None)
                # The rest (a fixture's teardown) is outside the pre-condition.
                item = first
                while True:
                    try:
                        sent = yield item
                    except GeneratorExit:
                        await gen.aclose()
                        raise
                    except BaseException as exc:
                        try:
                            item = await gen.athrow(exc)
                        except StopAsyncIteration:
                            return
                    else:
                        try:
                            item = await gen.asend(sent)
                        except StopAsyncIteration:
                            return

            return agen_wrapper  # type: ignore[return-value]

        if inspect.isgeneratorfunction(fn):

            @functools.wraps(fn)
            def gen_wrapper(*args: Any, **kwargs: Any) -> Any:
                gen = fn(*args, **kwargs)
                b = block()
                b._enter(where)
                try:
                    first = next(gen)
                except StopIteration as stop:
                    b.__exit__(None, None, None)
                    return stop.value
                except BaseException as exc:
                    if not b.__exit__(type(exc), exc, exc.__traceback__):
                        raise
                    yield None  # dry-run: the failure is recorded, the test goes on
                    return None
                b.__exit__(None, None, None)
                # The rest (a fixture's teardown) is outside the pre-condition.
                item = first
                while True:
                    try:
                        sent = yield item
                    except GeneratorExit:
                        gen.close()
                        raise
                    except BaseException as exc:
                        try:
                            item = gen.throw(exc)
                        except StopIteration as stop:
                            return stop.value
                    else:
                        try:
                            item = gen.send(sent)
                        except StopIteration as stop:
                            return stop.value

            return gen_wrapper  # type: ignore[return-value]

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                b = block()
                b._enter(where)
                try:
                    result = await fn(*args, **kwargs)
                except BaseException as exc:
                    if not b.__exit__(type(exc), exc, exc.__traceback__):
                        raise
                    return None
                b.__exit__(None, None, None)
                return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            b = block()
            b._enter(where)
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                if not b.__exit__(type(exc), exc, exc.__traceback__):
                    raise
                return None
            b.__exit__(None, None, None)
            return result

        return wrapper  # type: ignore[return-value]


class repeat:  # noqa: N801 - reads like ``step``
    """``with repeat("Drain the FIFO", until="FIFO is empty"):`` around a loop (ACT-10).

    Loops collapse on their own: when the same actions come back to back, the
    passes become one ``Repeat`` entry with a count. This block says what the loop
    does and what it waits for (all optional), and keeps the actions recorded
    before and after it out of the loop. The last test of a ``while`` condition (a
    pass cut short where the loop ended) belongs to the loop; a pass that differs,
    such as the one where an exception was raised, follows it as ordinary entries.

    With a title the block is a step, at the top level or inside another step:
    ``2. Drain the FIFO (repeat until FIFO is empty)``, its entries one pass
    (``Step.loop`` holds ``until`` and the count). Without one it is a ``Repeat``
    entry of the step it is in: ``1.2. Repeat until FIFO is empty``.

    ``counter="write_count"`` names the test's variable that counts the passes,
    ``inc=4`` how much it grows each pass (needs ``counter``). ``note=`` is a
    sentence for the reader, shown under the line, as on ``step``.
    """

    def __init__(
        self,
        title: Optional[str] = None,
        *,
        until: Optional[str] = None,
        counter: Optional[str] = None,
        inc: Union[int, float, None] = None,
        note: Optional[str] = None,
    ) -> None:
        if counter is not None and not isinstance(counter, str):
            raise TypeError(f"counter must be the variable's name (a str), not {type(counter).__name__}")
        if inc is not None:
            if isinstance(inc, bool) or not isinstance(inc, (int, float)):
                raise TypeError(f"inc must be a number, not {type(inc).__name__}")
            if counter is None:
                raise TypeError("inc= says how much counter= grows each pass; give counter= too")
        self.title = title
        self.until = until
        self.counter = counter
        self.inc = inc
        self.note = _note(note)
        self._rec: Optional[Recorder] = None
        self._step: Optional[Step] = None
        self._start = 0
        self._saved: Optional[int] = None
        self._outer: Optional[Step] = None

    def __enter__(self) -> None:
        rec = _current.get()
        if rec is None or rec._quiet:
            return None
        self._rec = rec
        self._outer = None
        if self.title:  # a step of its own; its entries are the pass
            self._outer = rec._open(self.title, _caller_location(), self.note)
        self._step = rec.current_step
        rec._flush(self._step)
        entries = rec._container(self._step)
        self._start = len(entries)
        self._saved = rec._floors.get(id(entries))
        rec._floors[id(entries)] = self._start
        return None

    def __exit__(
        self,
        et: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> bool:
        rec = self._rec
        if rec is None:
            return False
        entries = rec._container(self._step)
        if self._saved is None:
            rec._floors.pop(id(entries), None)
        else:
            rec._floors[id(entries)] = self._saved
        outer = self._outer
        rec._close_repeat(self._step, self._start, self.until, clean=exc is None)
        if outer is None:
            rep = entries[self._start]
            assert isinstance(rep, Repeat)
            rep.note, rep.counter, rep.inc = self.note, self.counter, self.inc
            return False
        rep, *rest = outer.entries
        assert isinstance(rep, Repeat)
        outer.entries = [*rep.entries, *rest]
        outer.loop = Loop(
            count=rep.count, until=self.until, counter=self.counter, inc=self.inc, t_last=rep.t_last
        )
        _renumber(outer)
        return rec._close(outer, exc)


def _note(note: Optional[str]) -> Optional[str]:
    """A ``note=`` as stored: indentation of a triple-quoted string removed, blank
    text dropped."""
    if note is None:
        return None
    if not isinstance(note, str):
        raise TypeError(f"note must be a str, not {type(note).__name__}")
    return inspect.cleandoc(note) or None


# (code, offset of the call) -> whether its result is discarded (STEP-9).
_STATEMENT_CALLS: dict[tuple[Any, int], bool] = {}
_DISCARD = frozenset({"POP_TOP"})


def _is_statement(frame: Any) -> bool:  # noqa: ANN401
    """True when the call running in ``frame`` is an expression statement: its
    result is dropped, so it is neither ``with step(...)`` nor ``@step(...)``."""
    code, offset = frame.f_code, frame.f_lasti
    key = (code, offset)
    found = _STATEMENT_CALLS.get(key)
    if found is None:
        found = False
        after = False
        for ins in dis.get_instructions(code):
            if after:
                found = ins.opname in _DISCARD
                break
            if ins.offset == offset:
                after = True
        _STATEMENT_CALLS[key] = found
    return found


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
