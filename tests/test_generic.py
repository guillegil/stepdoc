"""OUT-7: the generic procedure as JSON, one entry per test (--stepdoc-generic)."""

import json
from pathlib import Path

import jsonschema
import pytest

from stepdoc.cli import main
from stepdoc.renderers import render

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "src/stepdoc/schema/generic.schema.json").read_text()
)

CONFTEST = '''
import pytest
import stepdoc


class Device:
    def __init__(self):
        self.regs = {}

    def write(self, reg, value):
        self.regs[reg] = value
        stepdoc.record_action(reg, "write", value, value_from=("value",))

    def read(self, reg):
        return self.regs.get(reg, 0)


@pytest.fixture
def dev():
    with stepdoc.step("Power on"):
        d = Device()
        d.write("pwr.en", 1)
    yield d
    with stepdoc.step("Power off"):
        d.write("pwr.en", 0)
'''

TESTS = '''
import pytest
from stepdoc import step


class TestDac:
    @pytest.mark.parametrize("level", [10, 20, 30])
    def test_levels(self, dev, level):
        """Sets the DAC.

        Objective: every level is stored.
        """
        with step("Set level", note="one write"):
            dev.write("dac.level", level)
            assert dev.read("dac.level") == level


@pytest.mark.parametrize("mode", [1, 2])
def test_modes(dev, mode):
    with step("Configure"):
        if mode == 1:
            dev.write("ctrl.fast", 1)
        else:
            dev.write("ctrl.slow", 1)


def test_plain(dev):
    dev.write("ctrl.mode", 3)
'''


@pytest.fixture
def run(pytester):
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(test_dac=TESTS)
    pytester.makeini("[pytest]\nenable_assertion_pass_hook = true\nstepdoc_skip_modules = conftest\n")
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider",
        "--stepdoc-generic=out/step-generic.json",
        "--stepdoc-record=out/run.json",
        "--stepdoc-procedure=out/procedure.txt",
    )
    result.assert_outcomes(passed=6)
    assert "stepdoc: wrote out/step-generic.json" in result.stdout.str()
    return pytester


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def by_id(doc):
    return {t["test_id"]: t for t in doc["tests"]}


def texts(nodes, depth=0):
    for n in nodes:
        yield "   " * depth + (f"{n['number']}. " if n["number"] else "- ") + n["text"]
        yield from texts(n.get("steps", []), depth + 1)


def test_one_entry_per_test_whatever_its_cases(run):
    doc = load(run.path / "out/step-generic.json")
    jsonschema.validate(doc, SCHEMA)
    tests = by_id(doc)
    assert list(tests) == [
        "test_dac.py::TestDac::test_levels",
        "test_dac.py::test_modes",
        "test_dac.py::test_plain",
    ]
    levels = tests["test_dac.py::TestDac::test_levels"]
    assert levels["test_name"] == "test_levels"
    assert levels["test_file"] == "test_dac.py"
    assert levels["test_path"] == "test_dac.py"
    assert levels["cases"] == 3
    assert levels["parameters"] == [{"name": "level", "description": None, "values": ["10", "20", "30"]}]
    assert levels["doc"] == {"description": "Sets the DAC.", "objective": "every level is stored."}
    assert tests["test_dac.py::test_plain"]["cases"] == 1
    assert "parameters" not in tests["test_dac.py::test_plain"]


def test_steps_are_the_generic_procedure(run):
    levels = by_id(load(run.path / "out/step-generic.json"))["test_dac.py::TestDac::test_levels"]
    assert [(s["section"], s["number"], s["text"]) for s in levels["steps"]] == [
        ("setup", "S1", "Power on"),
        ("procedure", "1", "Set level"),
        ("teardown", "T1", "Power off"),
    ]
    step = levels["steps"][1]
    assert step["note"] == "one write"
    assert step["steps"] == [
        {"kind": "action", "number": "1.1", "section": "procedure", "text": "dac.level = <level>",
         "code": "dac.level = <level>"},
        {"kind": "check", "number": "1.2", "section": "procedure",
         "text": "Verify dev.read(\"dac.level\") == level", "verb": "Verify",
         "code": "dev.read(\"dac.level\") == level"},
    ]
    # Line for line the procedure's text (headings aside).
    procedure = (run.path / "out/procedure.txt").read_text().splitlines()
    for line in texts(levels["steps"]):
        assert line in procedure


def test_entries_outside_a_step(run):
    plain = by_id(load(run.path / "out/step-generic.json"))["test_dac.py::test_plain"]
    loose = [s for s in plain["steps"] if s["section"] == "procedure"]
    assert loose == [{"kind": "action", "number": None, "section": "procedure", "text": "ctrl.mode = 3",
                      "code": "ctrl.mode = 3"}]


def test_cases_that_took_different_paths_are_variants(run):
    modes = by_id(load(run.path / "out/step-generic.json"))["test_dac.py::test_modes"]
    assert modes["cases"] == 2
    variants = modes["variants"]
    assert [v["cases"] for v in variants] == [["1"], ["2"]]
    assert [list(texts(v["steps"])) for v in variants] == [
        ["1. Configure", "   1.1. ctrl.fast = 1"],
        ["1. Configure", "   1.1. ctrl.slow = 1"],
    ]
    procedure = [s for s in modes["steps"] if s["section"] == "procedure"]
    assert procedure == variants[0]["steps"]


def test_no_results_in_the_generic_procedure(run):
    text = (run.path / "out/step-generic.json").read_text()
    for word in ('"status"', '"passed"', '"outcome"', '"actual"', "✓"):
        assert word not in text


def test_render_from_a_saved_record(run, capsys):
    out = run.path / "again.json"
    assert main(["render", str(run.path / "out/run.json"), "--generic", str(out)]) == 0
    assert f"stepdoc: wrote {out}" in capsys.readouterr().out
    again, first = load(out), load(run.path / "out/step-generic.json")
    assert again["tests"] == first["tests"]


def test_render_generic_needs_no_pytest(run):
    from stepdoc.renderers.generic import render_generic

    record = load(run.path / "out/run.json")
    doc = json.loads(render_generic(record))
    jsonschema.validate(doc, SCHEMA)
    assert doc["schema_version"] == "1.0"
    assert render(record, "procedure", "text")  # the other documents still render
