"""pytest plugin: one recorder per test, parameters, assertions, dry-run, outputs.

Registered through the ``pytest11`` entry point, so it loads in every pytest run
where stepdoc is installed. It stays inert (no recorder, no output) unless a
stepdoc output option or ``--stepdoc-dry-run`` is passed (NFR-8).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Generator, Optional

import pytest

from .core.model import Check, Location
from .core.record import case_record, dumps, run_record
from .core.recorder import Recorder, current_recorder, is_dry_run
from .core.symbolic import set_skip_modules
from .renderers import markdown

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
        self.cases: list[dict[str, Any]] = []


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("stepdoc", "test documentation (stepdoc)")
    group.addoption("--stepdoc-record", metavar="PATH", default=None,
                    help="write the JSON run record (the source of every document)")
    group.addoption("--stepdoc-procedure", metavar="PATH", default=None,
                    help="write the procedure (Markdown) with symbolic values")
    group.addoption("--stepdoc-report", metavar="PATH", default=None,
                    help="write the executed report (Markdown) with concrete values")
    group.addoption("--stepdoc-dry-run", action="store_true", default=False,
                    help="collect the procedure without judging results; "
                         "tests can switch to simulated backends via the stepdoc_dry_run fixture")
    parser.addini("stepdoc_skip_modules", type="linelist", default=[],
                  help="module prefixes skipped when looking for the user's source line "
                       "(drivers, clients, bridges' libraries)")


def pytest_configure(config: pytest.Config) -> None:
    state = _State(config)
    config.stash[_state_key] = state
    if state.active and hasattr(config, "workerinput"):
        raise pytest.UsageError("stepdoc does not support pytest-xdist yet; run without -n")
    skip = config.getini("stepdoc_skip_modules")
    if skip:
        set_skip_modules(skip)


@pytest.fixture
def stepdoc_dry_run(request: pytest.FixtureRequest) -> bool:
    """True when running with ``--stepdoc-dry-run`` (DRY-2)."""
    return is_dry_run(request.config)


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
    rec = Recorder(item.nodeid, dry_run=state.dry_run)
    item.stash[_recorder_key] = rec
    try:
        with rec:
            return (yield)
    finally:
        case, params = _params(item)
        procedure_id = item.nodeid.split("[", 1)[0]
        state.cases.append(
            case_record(
                rec,
                root=state.root,
                test_id=item.nodeid,
                procedure_id=procedure_id,
                case=case,
                params=params,
                outcome=item.stash.get(_outcome_key, None),
            )
        )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Generator[None, Any, Any]:
    report = yield
    if _recorder_key in item.stash:
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
    if rec is None or _recorder_key not in item.stash:
        return
    rec.add_check(Check(text=orig, passed=True, location=Location(str(item.path), lineno)))


def pytest_sessionfinish(session: pytest.Session) -> None:
    state: Optional[_State] = session.config.stash.get(_state_key, None)
    if state is None or not state.active or not state.cases:
        return
    record = run_record(state.cases, dry_run=state.dry_run)
    written = []
    if state.record_path:
        written.append(_write(state.record_path, dumps(record)))
    if state.procedure_path:
        written.append(_write(state.procedure_path, markdown.render_procedure(record)))
    if state.report_path:
        written.append(_write(state.report_path, markdown.render_report(record)))
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
