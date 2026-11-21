"""pytest plugin: one recorder per test, parameters, assertions, dry-run, outputs.

Registered through the ``pytest11`` entry point, so it loads in every pytest run
where stepdoc is installed. It stays inert (no recorder, no output) unless a
stepdoc output option or ``--stepdoc-dry-run`` is passed (NFR-8).
"""

from __future__ import annotations

import io
import json
import os
import sys
import tokenize
from pathlib import Path
from types import FrameType
from typing import Any, Generator, Iterable, Optional

import pytest

from . import adapters as _adapters
from .core.docstring import parse_docstring
from .core.model import Check, Location, Section, Step
from .core.record import case_record, dumps, run_record
from .core.recorder import Recorder, _perf, _renumber, current_recorder, is_dry_run
from .core.symbolic import set_skip_modules
from .renderers import format_for_path, render

_state_key = pytest.StashKey["_State"]()
_recorder_key = pytest.StashKey[Recorder]()
_outcome_key = pytest.StashKey[str]()
_ran_key = pytest.StashKey["set[int]"]()
"""ids of the fixture definitions that ran (were not reused) for the test so far."""
_told_key = pytest.StashKey["set[int]"]()
"""ids of the pre-conditions already listed as established in this attempt."""


class _State:
    def __init__(self, config: pytest.Config) -> None:
        opt = config.option
        self.record_path: Optional[str] = opt.stepdoc_record
        self.procedure_path: Optional[str] = opt.stepdoc_procedure
        self.report_path: Optional[str] = opt.stepdoc_report
        self.dry_run: bool = opt.stepdoc_dry_run
        self.active = bool(self.record_path or self.procedure_path or self.report_path or self.dry_run)
        self.root = str(config.rootpath)
        self.collapse_repeats: bool = config.getini("stepdoc_collapse_repeats")
        self.comment_notes: bool = config.getini("stepdoc_comment_notes")
        self.cases: list[dict[str, Any]] = []
        # pytest-xdist (PAR-1): workers record, the controller merges and writes.
        self.worker = hasattr(config, "workerinput")
        self.order: dict[str, int] = {}  # test id -> collection index
        self.adapters: list[_adapters.Adapter] = []
        self.unseen_judges: list[_adapters.Adapter] = []  # judging tools whose adapter is off (NFR-7)
        # PRE-3: pre-conditions by the fixtures that were running when they were applied.
        self.running: list[Any] = []  # fixture definitions being set up, outermost first
        self.applied: dict[int, tuple[Any, list[tuple[str, Step]]]] = {}
        """id(fixture definition) -> the definition (kept alive, so the id stays
        its own) and the pre-conditions its last run applied, each with the id of
        the test it ran for."""


_WORKER_KEY = "stepdoc_cases"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("stepdoc", "test documentation (stepdoc)")
    group.addoption("--stepdoc-record", metavar="PATH", default=None,
                    help="write the JSON run record (the source of every document)")
    group.addoption("--stepdoc-procedure", metavar="PATH", default=None,
                    help="write the procedure with symbolic values (.md, .html or .txt)")
    group.addoption("--stepdoc-report", metavar="PATH", default=None,
                    help="write the executed report with concrete values (.md, .html or .txt)")
    group.addoption("--stepdoc-dry-run", action="store_true", default=False,
                    help="collect the procedure without judging results; "
                         "tests can switch to simulated backends via the stepdoc_dry_run fixture")
    parser.addini("stepdoc_skip_modules", type="linelist", default=[],
                  help="module prefixes skipped when looking for the user's source line "
                       "(drivers, clients, bridges' libraries)")
    parser.addini("stepdoc_collapse_repeats", type="bool", default=True,
                  help="collapse polling, repeated writes and loop passes into one entry")
    parser.addini("stepdoc_comment_notes", type="bool", default=True,
                  help="take a check's note from the comment on or above its line "
                       "when it has no note= (asserts and adapters' checks)")
    parser.addini("stepdoc_adapters", type="linelist", default=[],
                  help="adapters to run (default: every adapter whose tool is installed); "
                       "'-name' leaves one out, 'none' runs none")


def pytest_addhooks(pluginmanager: pytest.PytestPluginManager) -> None:
    from . import hookspecs

    pluginmanager.add_hookspecs(hookspecs)


def pytest_configure(config: pytest.Config) -> None:
    state = _State(config)
    config.stash[_state_key] = state
    skip = config.getini("stepdoc_skip_modules")
    if skip:
        set_skip_modules(skip)
    if state.active:
        _start_adapters(config, state)


def _start_adapters(config: pytest.Config, state: _State) -> None:
    known = _adapters.discover()
    state.adapters = _adapters.select(known, config, config.getini("stepdoc_adapters"))
    on = {a.name for a in state.adapters}
    state.unseen_judges = [a for a in known.values() if a.judges and a.name not in on and a.installed(config)]
    host = _adapters.Host(config)
    for adapter in state.adapters:
        plugin = adapter.activate(host)
        if plugin is not None:
            config.pluginmanager.register(plugin, f"stepdoc-adapter-{adapter.name}")


def recorder_for(item: pytest.Item) -> Optional[Recorder]:
    """The recorder of a test while stepdoc records it, for adapters."""
    return item.stash.get(_recorder_key, None)


@pytest.fixture(scope="session")
def stepdoc_dry_run(request: pytest.FixtureRequest) -> bool:
    """True when running with ``--stepdoc-dry-run`` (DRY-2). Session-scoped, so
    session fixtures (a device, a mock server) can use it too."""
    return is_dry_run(request.config)


def pytest_collection_finish(session: pytest.Session) -> None:
    # Cases are written in collection order, whichever worker ran them first.
    state: _State = session.config.stash[_state_key]
    state.order = {item.nodeid: i for i, item in enumerate(session.items)}


def _params(item: pytest.Item) -> tuple[Optional[str], dict[str, Any]]:
    callspec = getattr(item, "callspec", None)
    if callspec is None:
        return None, {}
    return callspec.id, dict(callspec.params)


def _doc(item: pytest.Item) -> Optional[dict[str, str]]:
    """DOC-1: the test function's own docstring, parsed. Not a class's or a
    module's, and not one inherited."""
    if not isinstance(item, pytest.Function):
        return None
    text = getattr(getattr(item, "function", None), "__doc__", None)
    return parse_docstring(text) if isinstance(text, str) else None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: Optional[pytest.Item]) -> Generator[None, Any, Any]:
    state: _State = item.config.stash[_state_key]
    if not state.active:
        return (yield)
    rec = Recorder(
        item.nodeid,
        dry_run=state.dry_run,
        collapse_repeats=state.collapse_repeats,
        comment_notes=state.comment_notes,
    )
    item.stash[_recorder_key] = rec

    def notify(event: str, s: Step) -> None:
        if event == "finished" and s.section == "precondition" and "." not in s.number and s.applied_in is None:
            for fixturedef in state.running:
                state.applied[id(fixturedef)][1].append((item.nodeid, s))
        hook = item.ihook.pytest_stepdoc_step_started if event == "started" else item.ihook.pytest_stepdoc_step_finished
        hook(item=item, step=s)

    rec.listeners.append(notify)
    doc = _doc(item)
    if doc:
        item.ihook.pytest_stepdoc_test_doc(item=item, doc=dict(doc))
    try:
        with rec:
            return (yield)
    finally:
        case, params = _params(item)
        procedure_id = item.nodeid.split("[", 1)[0]
        extra: dict[str, Any] = {}
        for adapter in state.adapters:
            extra.update(adapter.case(item))
        if any(judge.uses(item) for judge in state.unseen_judges):
            rec.mark_unknown()
        state.cases.append(
            case_record(
                rec,
                root=state.root,
                test_id=item.nodeid,
                procedure_id=procedure_id,
                case=case,
                params=params,
                outcome=item.stash.get(_outcome_key, None),
                doc=doc,
                **extra,
            )
        )


def _in_section(item: pytest.Item, section: Section) -> None:
    rec = item.stash.get(_recorder_key, None)
    if rec is not None:
        rec.section = section


# STEP-8: steps run by fixtures go into the Setup and Teardown sections.
@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, None, None]:
    _in_section(item, "setup")
    item.stash[_ran_key] = set()
    item.stash[_told_key] = set()
    raised: Optional[BaseException] = None
    try:
        return (yield)
    except BaseException as exc:
        raised = exc
        raise
    finally:
        _in_section(item, "procedure")
        _established(item, raised)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, None, None]:
    try:
        return (yield)
    finally:
        # A shared fixture the test body asked for (request.getfixturevalue).
        _established(item, None)


@pytest.hookimpl(wrapper=True)
def pytest_fixture_setup(fixturedef: Any, request: Any) -> Generator[None, Any, Any]:
    """PRE-3: remember which fixture applies which pre-conditions. pytest calls
    this only when a fixture really runs, not when its cached value is reused."""
    state: Optional[_State] = request.config.stash.get(_state_key, None)
    if state is None or not state.active:
        return (yield)
    state.applied[id(fixturedef)] = (fixturedef, [])
    item = getattr(request, "_pyfuncitem", None) or getattr(request, "node", None)
    stash = getattr(item, "stash", None)
    if stash is not None:
        stash.setdefault(_ran_key, set()).add(id(fixturedef))
    state.running.append(fixturedef)
    try:
        return (yield)
    finally:
        state.running.remove(fixturedef)


def _fixturedefs(item: pytest.Item, state: _State, raised: Optional[BaseException]) -> Iterable[Any]:
    """The fixture definitions this run of ``item`` really reached: the ones its
    request resolved (with the definitions they override, when they ask for their
    own name), and the one whose cached error ended the setup (pytest raises a
    failed shared fixture's error again without listing it). A test skipped or
    failed before a fixture was reached does not use it."""
    request = getattr(item, "_request", None)
    resolved = getattr(request, "_fixture_defs", None)
    if isinstance(resolved, dict):
        info = getattr(item, "_fixtureinfo", None)
        by_name = getattr(info, "name2fixturedefs", None) or {}
        for name, fixturedef in list(resolved.items()):
            yield fixturedef
            for overridden in reversed(by_name.get(name) or ()):
                yield overridden
                if name not in getattr(overridden, "argnames", ()):
                    break
    if raised is None:
        return
    for fixturedef, _ in list(state.applied.values()):
        cached = getattr(fixturedef, "cached_result", None)
        error = cached[2] if cached is not None else None
        if isinstance(error, tuple) and any(part is raised for part in error):
            yield fixturedef


def _established(item: pytest.Item, raised: Optional[BaseException]) -> None:
    """PRE-3: a test that uses a fixture whose pre-conditions an earlier test
    applied (a session, module or class fixture, not run again) lists them as
    already established: before its own, in the order they were applied."""
    rec = item.stash.get(_recorder_key, None)
    if rec is None:
        return
    state: _State = item.config.stash[_state_key]
    ran = item.stash.get(_ran_key, set())
    told = item.stash.setdefault(_told_key, set())
    found: dict[int, tuple[str, Step]] = {}
    for fixturedef in _fixturedefs(item, state, raised):
        if id(fixturedef) in ran or getattr(fixturedef, "cached_result", None) is None:
            continue
        entry = state.applied.get(id(fixturedef))
        if entry is None or entry[0] is not fixturedef:
            continue
        for test_id, original in entry[1]:
            if id(original) not in told:
                found.setdefault(id(original), (test_id, original))
    if not found:
        return
    told.update(found)
    present = {id(s.origin or s): s for s in rec.steps if s.section == "precondition"}
    refs: list[Step] = []
    again: list[Step] = []
    now = _perf()
    for test_id, original in sorted(found.values(), key=lambda pair: pair[1].started):
        ref = Step(
            number="",
            title=original.title,
            section="precondition",
            status=original.status,
            started=now,
            ended=now,
            error=original.error,
            location=original.location,
            note=original.note,
            applied_in=test_id,
            origin=original,
        )
        (again if id(original) in present else refs).append(ref)
    if refs:
        # Only the references move to the front: the test's own steps stay in the
        # order they ran, which adapters that place late checks rely on.
        lead = next((i for i, s in enumerate(rec.steps) if s.applied_in is None), len(rec.steps))
        rec.steps[lead:lead] = refs
        for n, s in enumerate((s for s in rec.steps if s.section == "precondition"), 1):
            if s.number != f"P{n}":
                s.number = f"P{n}"
                _renumber(s)
    # A rerun of the test (pytest-reporter's retries run inside one recorder): the
    # record lists the pre-condition already, but plugins that follow each attempt
    # are told again.
    for ref in again:
        ref.number = present[id(ref.origin)].number
    for ref in [*refs, *again]:
        rec._notify("started", ref)
        rec._notify("finished", ref)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: Optional[pytest.Item]) -> Generator[None, None, None]:
    _in_section(item, "teardown")
    return (yield)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Generator[None, Any, Any]:
    report = yield
    rec = item.stash.get(_recorder_key, None)
    if rec is not None:
        # CHK-9: comment notes before the phase's report is logged, so tools that
        # read the steps at pytest_runtest_logfinish see them.
        rec.resolve_notes()
        current = item.stash.get(_outcome_key, None)
        if report.skipped:
            outcome = "skipped"
        elif report.failed:
            outcome = "failed" if report.when == "call" else "error"
        else:
            outcome = "passed"
        # The worst phase wins: an error in teardown overrides a passed call.
        order = {None: 0, "passed": 1, "skipped": 2, "failed": 3, "error": 4}
        if order[outcome] > order[current]:
            item.stash[_outcome_key] = outcome
    return report


def pytest_assertion_pass(item: pytest.Item, lineno: int, orig: str, expl: str) -> None:
    """CHK-4: passing ``assert`` statements become checks. Needs the
    ``enable_assertion_pass_hook`` ini option."""
    rec = current_recorder()
    if rec is None or _recorder_key not in item.stash or _asserted_by_stepdoc():
        return
    check = Check(text=_code_only(orig), passed=True, location=Location(str(item.path), lineno))
    check.note_from_comment = True
    rec.add_check(check)


_PACKAGE = os.path.dirname(os.path.abspath(__file__)) + os.sep


def _asserted_by_stepdoc() -> bool:
    """True for an ``assert`` in stepdoc's own code. pytest rewrites the asserts
    of plugins installed in editable mode too, and recording one of them as a
    check would record stepdoc's internals (and recurse while adding it)."""
    f: Optional[FrameType] = sys._getframe(2)
    for _ in range(16):
        if f is None:
            return False
        if f.f_code.co_name == "_call_assertion_pass":
            caller = f.f_back
            return caller is not None and os.path.abspath(caller.f_code.co_filename).startswith(_PACKAGE)
        f = f.f_back
    return False


def _code_only(source: str) -> str:
    """An assert's source without the comments pytest keeps in it on some versions
    (they become the check's note, CHK-9)."""
    if "#" not in source:
        return source
    try:
        tokens = [t for t in tokenize.generate_tokens(io.StringIO(source).readline) if t.type == tokenize.COMMENT]
    except (tokenize.TokenError, SyntaxError):
        return source
    lines = source.splitlines()
    for t in reversed(tokens):
        row, col = t.start
        lines[row - 1] = lines[row - 1][:col].rstrip()
    return "\n".join(line for line in lines if line.strip()) or source


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: Any) -> None:
    """pytest-xdist controller: collect the cases a worker recorded."""
    state: Optional[_State] = node.config.stash.get(_state_key, None)
    output = getattr(node, "workeroutput", None) or {}
    if state is None or _WORKER_KEY not in output:
        return  # inactive, or the worker crashed before sending anything
    payload = json.loads(output[_WORKER_KEY])
    state.cases.extend(payload["cases"])
    for test_id, index in payload["order"].items():
        state.order.setdefault(test_id, index)


def pytest_sessionfinish(session: pytest.Session) -> None:
    state: Optional[_State] = session.config.stash.get(_state_key, None)
    if state is None or not state.active:
        return
    if state.worker:
        # Every value is already JSON-safe (to_jsonable); a string keeps it so
        # through execnet, which only carries plain built-in types.
        workeroutput = session.config.workeroutput  # type: ignore[attr-defined]
        workeroutput[_WORKER_KEY] = json.dumps({"cases": state.cases, "order": state.order})
        return
    if not state.cases:
        return
    last = len(state.order)
    cases = sorted(state.cases, key=lambda c: state.order.get(c["id"], last))
    record = run_record(cases, dry_run=state.dry_run)
    session.config.hook.pytest_stepdoc_record_ready(config=session.config, record=record)
    written = []
    if state.record_path:
        written.append(_write(state.record_path, dumps(record)))
    if state.procedure_path:
        fmt = format_for_path(state.procedure_path)
        written.append(_write(state.procedure_path, render(record, "procedure", fmt)))
    if state.report_path:
        fmt = format_for_path(state.report_path)
        written.append(_write(state.report_path, render(record, "report", fmt)))
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        for path in written:
            reporter.write_line(f"stepdoc: wrote {path}")


def _write(path: str, text: str) -> str:
    p = Path(path)
    if p.parent and not p.parent.exists():
        os.makedirs(p.parent, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return str(p)
