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


FIXTURE_TESTS = '''
import pytest
import stepdoc
from stepdoc import step


@pytest.fixture(scope="session")
def bench(dev):
    with step("Power up bench"):
        dev.write("bench.power", 1)
    yield
    with step("Power down bench"):
        dev.write("bench.power", 0)


@pytest.fixture
def configured(dev, bench):
    dev.write("ctrl.reset", 1)  # outside any step: unscoped, in Setup
    with step("Configure"):
        dev.write("ctrl.mode", 2)
    yield
    with step("Restore"):
        dev.write("ctrl.mode", 0)


@pytest.mark.parametrize("level", [1, 2])
def test_with_fixtures(dev, configured, level):
    with step("Set level"):
        dev.write("dac.level", level)
        assert dev.read("dac.level") == level
'''


READS = """
    def read(self, reg):
        value = self.regs.get(reg, 0)
        for hook in self.read_hooks:
            hook(reg, value)
        return value
"""

FIXTURE_CONFTEST = CONFTEST.replace(
    "@pytest.fixture\ndef dev", '@pytest.fixture(scope="session")\ndef dev'
).replace(
    "    d.simulated",
    '    d.read_hooks = [lambda reg, val: stepdoc.record_action(reg, "read", val)]\n    d.simulated',
)


@pytest.fixture
def fixture_project(pytester):
    pytester.makeconftest(FIXTURE_CONFTEST)
    driver = DRIVER.split("    def read(self, reg):")[0] + READS
    pytester.makepyfile(test_fx=FIXTURE_TESTS, mydevice=driver)
    pytester.makeini("[pytest]\nenable_assertion_pass_hook = true\nstepdoc_skip_modules = mydevice\n")
    return pytester


def test_fixture_steps_go_to_setup_and_teardown(fixture_project):
    result = run(fixture_project, "--stepdoc-record=run.json", "--stepdoc-procedure=procedure.md")
    result.assert_outcomes(passed=2)
    record = json.loads((fixture_project.path / "run.json").read_text())
    jsonschema.validate(record, SCHEMA)
    first, second = record["tests"]

    def numbered(test):
        return [(s["section"], s["number"], s["title"]) for s in test["steps"]]

    assert numbered(first) == [
        ("setup", "S1", "Power up bench"),
        ("setup", "S2", "Configure"),
        ("procedure", "1", "Set level"),
        ("teardown", "T1", "Restore"),
    ]
    # The session fixture tears down after the last test only.
    assert numbered(second)[-2:] == [("teardown", "T1", "Restore"), ("teardown", "T2", "Power down bench")]
    assert first["unscoped"][0]["section"] == "setup"

    # The passing assert absorbed the read on its line (ACT-5).
    set_level = first["steps"][2]["entries"]
    assert [e["type"] for e in set_level] == ["action", "check"]
    check = set_level[1]
    assert check["number"] == "1.2" and check["passed"] is True
    assert [(r["target"]["concrete"], r["value"]["concrete"]) for r in check["reads"]] == [("dac.level", 1)]

    # One procedure for both cases, with the fullest setup and teardown.
    procedure = (fixture_project.path / "procedure.md").read_text()
    assert "Procedure for" not in procedure
    for text in ("**Setup**", "**S1. Power up bench**", "**Procedure**", "**T2. Power down bench**"):
        assert text in procedure
    assert procedure.index("**S2. Configure**") < procedure.index("**1. Set level**") < procedure.index("**T1. Restore**")


def test_format_follows_the_file_extension(project):
    result = run(project, "--stepdoc-procedure=out/procedure.html", "--stepdoc-report=out/report.txt")
    result.assert_outcomes(passed=2, failed=1)
    assert (project.path / "out/procedure.html").read_text().startswith("<!doctype html>")
    assert "test_dev.py::test_levels[20]   FAILED" in (project.path / "out/report.txt").read_text()
