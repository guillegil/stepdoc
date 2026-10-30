"""Adapters (verify, strategies), stepdoc's own hooks and the adapter switch.

pytest-verifier and pytest-strategies are not installed here: small fakes stand
in for them, publishing the same facts the real tools publish (verify's
``pytest_verify_results`` hook with its descriptors; strategies' ``strategy``
marks holding a ``to_dict()`` row and ``export_strategies()``)."""

import json
from pathlib import Path

import jsonschema
import pytest

from stepdoc.adapters import Adapter, discover, select
from stepdoc.adapters.strategies import domain

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "src/stepdoc/schema/run-record.schema.json").read_text()
)

VERIFY_SPECS = '''
import pytest


@pytest.hookspec
def pytest_verify_results(item, when, checks, passed):
    """Same signature as pytest-verifier's."""
'''

# A fake pytest-verifier: the ``verify`` fixture records descriptors shaped like
# the real ones and passes them to the hook when the test body ends.
FAKE_VERIFY = '''
import os
import sys

import pytest

_key = pytest.StashKey()


def pytest_addhooks(pluginmanager):
    import verify_specs

    pluginmanager.add_hookspecs(verify_specs)


class Verify:
    def __init__(self, item):
        self.item = item
        self.records = []

    def _where(self):
        root = str(self.item.config.rootpath)
        frame = sys._getframe(3)  # _where <- _add <- equal <- the caller
        here = f"{os.path.relpath(frame.f_code.co_filename, root)}:{frame.f_lineno}"
        test = getattr(self.item, "function", None)
        while frame is not None and test is not None and frame.f_code is not test.__code__:
            frame = frame.f_back
        where = {"location": here}
        if frame is not None:
            outer = f"{os.path.relpath(frame.f_code.co_filename, root)}:{frame.f_lineno}"
            if outer != here:
                where["called_from"] = outer
        return where

    def equal(self, actual, expected, *, name):
        return self._add("equal", name, f"Verify '{name}' == {expected!r}", actual == expected,
                         actual=actual, expected=expected)

    def approx(self, actual, expected, *, abs_tol, name, units=None):
        u = f" {units}" if units else ""
        return self._add("approx", name, f"Verify '{name}' == {expected!r}{u} ± {abs_tol!r}{u}",
                         abs(actual - expected) <= abs_tol, actual=actual, expected=expected,
                         abs_tol=abs_tol, units=units)

    def _add(self, kind, name, description, passed, **fields):
        d = {"check_type": kind, "name": name, "description": description, "passed": passed,
             "phase": "call", **self._where(), **fields}
        self.records.append(d)
        return d


@pytest.fixture
def verify(request):
    v = Verify(request.node)
    request.node.stash[_key] = v
    return v


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    result = yield
    v = item.stash.get(_key, None)
    if v is not None and v.records:
        passed = all(r["passed"] for r in v.records)
        item.ihook.pytest_verify_results(item=item, when="call", checks=list(v.records), passed=passed)
        if not passed:
            raise AssertionError("soft checks failed")
    return result
'''

VERIFY_TESTS = '''
from stepdoc import step


@step("Check stored user")
def check_user(verify, user, age):
    verify.equal(user["age"], age, name="Stored age")


def test_user(verify):
    age = 30
    with step("Create user"):
        verify.equal(201, 201, name="HTTP status")
        with step("Inner"):
            verify.approx(1.66, age * 0.055, abs_tol=0.05, name="Voltage", units="V")
        verify.equal("Ana", "Ana", name="Stored name")
    check_user(verify, {"age": 31}, age)
    for i in range(2):
        with step(f"Round {i}"):
            verify.equal(i, i, name="Round")


def test_plain():
    with step("Only asserts"):
        assert True
'''


@pytest.fixture
def verify_project(pytester):
    pytester.makepyfile(pytest_verifier=FAKE_VERIFY, verify_specs=VERIFY_SPECS, test_users=VERIFY_TESTS)
    return pytester


def run(pytester, *args):
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "pytest_verifier", *args)


def test_verify_checks_go_into_their_steps(verify_project):
    result = run(verify_project, "--stepdoc-record=run.json", "--stepdoc-procedure=procedure.txt",
                 "--stepdoc-report=report.txt")
    result.assert_outcomes(passed=1, failed=1)
    record = json.loads((verify_project.path / "run.json").read_text())
    jsonschema.validate(record, SCHEMA)
    procedure = (verify_project.path / "procedure.txt").read_text().splitlines()
    assert procedure[1:12] == [
        "1. Create user",
        "   1.1. Verify 'HTTP status' == 201",
        "   1.2. Inner",
        "      1.2.1. Verify 'Voltage' == <age * 0.055> V ± 0.05 V",
        "   1.3. Verify 'Stored name' == 'Ana'",
        "2. Check stored user",
        "   2.1. Verify 'Stored age' == <age>",
        "3. Round 0",
        "   3.1. Verify 'Round' == <i>",
        "4. Round 1",
        "   4.1. Verify 'Round' == <i>",
    ]
    report = (verify_project.path / "report.txt").read_text()
    assert "1.2.1. Verify 'Voltage' == 1.65 V ± 0.05 V -> got 1.66   ✓" in report
    assert "2. Check stored user   ✗" in report
    assert "2.1. Verify 'Stored age' == 30 -> got 31   ✗" in report
    assert "1. Create user   ✓" in report


def test_without_the_adapter_steps_are_unknown(verify_project):
    verify_project.makeini("[pytest]\nstepdoc_adapters = -verify\n")
    run(verify_project, "--stepdoc-record=run.json")
    user, plain = json.loads((verify_project.path / "run.json").read_text())["tests"]
    assert [s["status"] for s in user["steps"]] == ["unknown"] * 4  # never a false ✓ (NFR-7)
    assert user["outcome"] == "failed"  # the test's verdict still shows
    assert plain["steps"][0]["status"] == "passed"  # no verify in this test


def test_adapter_list_and_unknown_names(verify_project):
    verify_project.makeini("[pytest]\nstepdoc_adapters = nope\n")
    result = run(verify_project, "--stepdoc-record=run.json")
    result.stderr.fnmatch_lines(["*stepdoc_adapters: unknown adapter 'nope'*"])
    assert result.ret == pytest.ExitCode.USAGE_ERROR


def test_verify_checks_in_dry_run_are_not_judged(verify_project):
    run(verify_project, "--stepdoc-dry-run", "--stepdoc-record=run.json")
    user = json.loads((verify_project.path / "run.json").read_text())["tests"][0]
    check = user["steps"][1]["entries"][0]
    assert check["text"] == "'Stored age' == 30" and check["passed"] is None


STEP_HOOKS = '''
import json

import pytest

events = []


@pytest.hookimpl(optionalhook=True)
def pytest_stepdoc_step_started(item, step):
    events.append(["started", item.name, step.number, step.title])


@pytest.hookimpl(optionalhook=True)
def pytest_stepdoc_step_finished(item, step):
    events.append(["finished", item.name, step.number, step.status])


@pytest.hookimpl(optionalhook=True)
def pytest_stepdoc_record_ready(config, record):
    events.append(["record", len(record["tests"])])
    with open("events.json", "w") as f:
        json.dump(events, f)
'''


def test_stepdoc_hooks(pytester):
    pytester.makeconftest(STEP_HOOKS)
    pytester.makepyfile(
        """
        from stepdoc import step

        def test_it():
            with step("Outer"):
                with step("Inner"):
                    pass
        """
    )
    pytester.runpytest_subprocess("-p", "no:cacheprovider", "--stepdoc-record=run.json").assert_outcomes(passed=1)
    assert json.loads((pytester.path / "events.json").read_text()) == [
        ["started", "test_it", "1", "Outer"],
        ["started", "test_it", "1.1", "Inner"],
        ["finished", "test_it", "1.1", "passed"],
        ["finished", "test_it", "1", "passed"],
        ["record", 1],
    ]


def test_stepdoc_hooks_without_stepdoc_options(pytester):
    pytester.makeconftest(STEP_HOOKS)
    pytester.makepyfile("from stepdoc import step\n\ndef test_it():\n    with step('A'):\n        pass\n")
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=1)
    assert not (pytester.path / "events.json").exists()  # inert (NFR-8)


# A fake pytest-strategies: rows carry a ``strategy`` mark holding an object with
# ``to_dict()``, and ``export_strategies()`` describes the arguments.
FAKE_STRATEGIES = '''
import json

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "strategy(info): the row a test runs")


class Row:
    def __init__(self, kind, name, values):
        self.kind, self.name, self.values = kind, name, values

    def to_dict(self):
        return {"schema": 1, "strategy": "new_user", "origin": "test_new.py:4", "kind": self.kind,
                "name": self.name, "index": 0, "values": self.values, "id": "x", "seed": 1234,
                "context": None, "constraints_off": [], "streams": 1}


def export_strategies(*, format="json"):
    return json.dumps({"schema": 1, "kind": "strategies", "strategies": [{
        "name": "new_user",
        "origin": {"folder": ".", "file": "test_new.py", "qualname": "new_user", "line": 4},
        "parameter": {"arguments": [
            {"name": "name", "description": "User name", "source": "rng",
             "rng": {"type": "RNGString", "length": None, "min_length": 1, "max_length": 40}},
            {"name": "age", "description": "Age in years", "source": "rng",
             "rng": {"type": "RNGInteger", "min": 0, "max": 120, "predicate": False}},
        ]},
    }]})
'''

STRATEGY_TESTS = '''
import pytest
from pytest_strategy import Row
from stepdoc import step

ROWS = [
    pytest.param("Ana", 0, marks=pytest.mark.strategy(Row("directed", "youngest", {"name": "Ana", "age": 0}))),
    pytest.param("Kim", 57, marks=pytest.mark.strategy(Row("random", None, {"name": "Kim", "age": 57}))),
]


@pytest.mark.parametrize("name,age", ROWS)
def test_user(name, age):
    with step("Create user"):
        pass
'''


def test_strategies_seed_rows_and_domains(pytester):
    pytester.makepyfile(pytest_strategy=FAKE_STRATEGIES, test_new=STRATEGY_TESTS)
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "pytest_strategy", "--stepdoc-record=run.json",
        "--stepdoc-procedure=procedure.md", "--stepdoc-report=report.md",
    )
    result.assert_outcomes(passed=2)
    record = json.loads((pytester.path / "run.json").read_text())
    jsonschema.validate(record, SCHEMA)
    ana, kim = record["tests"]
    assert ana["seed"] == 1234
    assert ana["vector"] == {"source": "strategy", "strategy": "new_user", "kind": "directed", "name": "youngest"}
    assert ana["param_info"]["age"] == {"domain": "integer 0 … 120", "description": "Age in years"}
    procedure = (pytester.path / "procedure.md").read_text()
    assert "**Parameters** (strategy `new_user`)" in procedure
    assert "| `age` | Age in years | `integer 0 … 120` |" in procedure
    assert "| `name` | User name | `string, 1 … 40 characters` |" in procedure
    report = (pytester.path / "report.md").read_text()
    assert "seed 1234" in report
    assert "Parameters: `name = 'Ana'`, `age = 0` (directed: youngest)" in report
    assert "(random)" in report


def test_domain_texts():
    assert domain({"source": "value", "value": 5}) == "fixed 5"
    assert domain({"source": "rng", "rng": {"type": "RNGFloat", "min": 0.5, "max": 2.0}}) == "float 0.5 … 2.0"
    assert domain({"source": "rng", "rng": {"type": "RNGEnum", "enum": "Mode", "members": [
        {"$enum": "Mode", "member": "STANDBY"}, {"$enum": "Mode", "member": "BOOST"}]}}) == "Mode: STANDBY, BOOST"
    assert domain({"source": "rng", "rng": {"type": "Series", "sequence": [1, 2]}}) == "series 1, 2"
    assert domain({"source": "rng", "rng": {"type": "RNGChoice", "choices": ["a"]}}) == "one of 'a'"
    assert domain({"source": "rng", "rng": {"type": "Custom", "attributes": {}}}) == "Custom"


class Fake(Adapter):
    name = "fake"
    plugins = ("fake_tool",)


def test_entry_point_adapters(monkeypatch):
    class EP:
        def load(self):
            return Fake

    import stepdoc.adapters as adapters

    monkeypatch.setattr(adapters, "entry_points", lambda group: [EP()] if group == "stepdoc.adapters" else [])
    known = discover()
    assert set(known) == {"verify", "strategies", "fake"}

    class Config:
        class pluginmanager:
            @staticmethod
            def has_plugin(name):
                return name in ("fake_tool", "pytest_verifier")

    assert [a.name for a in select(known, Config, [])] == ["verify", "fake"]  # installed tools only
    assert [a.name for a in select(known, Config, ["fake"])] == ["fake"]
    assert [a.name for a in select(known, Config, ["-fake"])] == ["verify"]
    assert select(known, Config, ["none"]) == []
