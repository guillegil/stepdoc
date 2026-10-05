"""The pytest plugin, run in a separate pytest process (pytester)."""

import json
from pathlib import Path

import jsonschema
import pytest

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "src/stepdoc/schema/run-record.schema.json").read_text()
)

DRIVER = '''
class Device:
    """A tiny driver: every register write is an event."""

    def __init__(self):
        self.regs = {}
        self.hooks = []

    def write(self, reg, value):
        self.regs[reg] = value
        for hook in self.hooks:
            hook(reg, value)

    def read(self, reg):
        return self.regs.get(reg, 0)
'''

CONFTEST = '''
import pytest
import stepdoc
from mydevice import Device


@pytest.fixture
def dev(stepdoc_dry_run):
    d = Device()
    d.hooks.append(lambda reg, val: stepdoc.record_action(reg, "write", val, value_from=("value",)))
    d.simulated = stepdoc_dry_run
    return d
'''

TESTS = '''
import pytest
from stepdoc import step


@pytest.mark.parametrize("level", [10, 20])
def test_levels(dev, level):
    with step("Set level"):
        dev.write("dac.level", level * 2)
        assert dev.read("dac.level") == level * 2
    with step("Check"):
        assert level < 15, "level too high"
    with step("After the check"):
        dev.write("ctrl.enable", 1)


def test_plain(dev):
    dev.write("ctrl.mode", 3)
'''


@pytest.fixture
def project(pytester):
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(test_dev=TESTS, mydevice=DRIVER)
    pytester.makeini("[pytest]\nenable_assertion_pass_hook = true\nstepdoc_skip_modules = mydevice\n")
    return pytester


def run(pytester, *args):
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)


def test_inert_without_options(project):
    result = run(project)
    result.assert_outcomes(passed=2, failed=1)
    assert "stepdoc:" not in result.stdout.str()
    assert not [p for p in project.path.rglob("*") if p.suffix in (".json", ".md")]


def test_writes_record_procedure_and_report(project):
    result = run(
        project,
        "--stepdoc-record=out/run.json",
        "--stepdoc-procedure=out/procedure.md",
        "--stepdoc-report=out/report.md",
    )
    result.assert_outcomes(passed=2, failed=1)
    result.stdout.fnmatch_lines(["stepdoc: wrote out/run.json", "stepdoc: wrote out/procedure.md"])

    record = json.loads((project.path / "out/run.json").read_text())
    jsonschema.validate(record, SCHEMA)
    ids = [t["id"] for t in record["tests"]]
    assert ids == ["test_dev.py::test_levels[10]", "test_dev.py::test_levels[20]", "test_dev.py::test_plain"]

    ten, twenty, plain = record["tests"]
    assert ten["params"] == {"level": 10} and ten["case"] == "10"
    assert ten["procedure_id"] == twenty["procedure_id"] == "test_dev.py::test_levels"
    assert (ten["outcome"], twenty["outcome"]) == ("passed", "failed")
    assert twenty["status"] == "failed"
    assert plain["case"] is None and plain["unscoped"][0]["target"]["concrete"] == "ctrl.mode"

    # The failing assert is attached to its step, and the test stopped there.
    check_step = twenty["steps"][1]
    assert check_step["status"] == "failed"
    assert check_step["entries"][0]["text"] == "level < 15"
    assert check_step["entries"][0]["detail"].startswith("level too high")
    assert len(twenty["steps"]) == 2

    # Passing asserts are recorded through pytest_assertion_pass (CHK-4).
    set_level = ten["steps"][0]["entries"]
    assert [e["type"] for e in set_level] == ["action", "check"]
    assert set_level[1]["text"] == 'dev.read("dac.level") == level * 2'
    assert set_level[1]["passed"] is True

    procedure = (project.path / "out/procedure.md").read_text()
    assert "| `level` | `10`, `20` |" in procedure
    assert "**Procedure for" not in procedure  # the failed case stopped early, same procedure
    assert "1.1. `dac.level = <level * 2>`" in procedure
    report = (project.path / "out/report.md").read_text()
    assert "## test_levels[20] — FAILED" in report
    assert "`dac.level = 40`" in report


def test_dry_run_collects_the_whole_procedure(project):
    result = run(project, "--stepdoc-dry-run", "--stepdoc-record=run.json")
    result.assert_outcomes(passed=3)
    record = json.loads((project.path / "run.json").read_text())
    assert record["dry_run"] is True
    twenty = record["tests"][1]
    assert [s["title"] for s in twenty["steps"]] == ["Set level", "Check", "After the check"]
    assert twenty["steps"][1]["entries"][0]["passed"] is None
    assert {s["status"] for s in twenty["steps"]} == {"unknown"}


def test_dry_run_fixture(pytester):
    pytester.makepyfile(
        """
        def test_flag(stepdoc_dry_run, request):
            import stepdoc
            assert stepdoc_dry_run is stepdoc.is_dry_run(request.config)
            print("DRY", stepdoc_dry_run)
        """
    )
    assert "DRY False" in run(pytester, "-s").stdout.str()
    assert "DRY True" in run(pytester, "-s", "--stepdoc-dry-run").stdout.str()


def test_skip_modules_ini(pytester):
    pytester.makepyfile(
        driver="""
        import stepdoc

        def set_width(value):
            _low_level(value)

        def _low_level(v):
            stepdoc.record_action("width", "write", v, bridge_frames=0)
        """,
        test_skip="""
        from driver import set_width

        def test_it():
            w = 5
            set_width(w)
        """,
    )
    pytester.makeini("[pytest]\nstepdoc_skip_modules = driver\n")
    run(pytester, "--stepdoc-record=run.json").assert_outcomes(passed=1)
    (test,) = json.loads((pytester.path / "run.json").read_text())["tests"]
    assert test["unscoped"][0]["value"]["symbolic"] == "<w>"
