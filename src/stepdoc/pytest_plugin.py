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
from typing import Any, Generator, Optional

import pytest

from . import adapters as _adapters
from .core.model import Check, Location, Section, Step
from .core.record import case_record, dumps, run_record
from .core.recorder import Recorder, current_recorder, is_dry_run
from .core.symbolic import set_skip_modules
from .renderers import format_for_path, render

_state_key = pytest.StashKey["_State"]()
_recorder_key = pytest.StashKey[Recorder]()
_outcome_key = pytest.StashKey[str]()


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
        hook = item.ihook.pytest_stepdoc_step_started if event == "started" else item.ihook.pytest_stepdoc_step_finished
        hook(item=item, step=s)

    rec.listeners.append(notify)
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
    rec = item.stash.get(_recorder_key, None)
    if rec is not None:  # a new attempt (a rerun runs the protocol again)
        rec.attempt = len(rec.steps)
    _in_section(item, "setup")
    try:
        return (yield)
    finally:
        _in_section(item, "procedure")


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
