"""ACT-10: loops collapse into one Repeat entry; stepdoc.repeat(title, until=...) names them."""

import json
import sys
from pathlib import Path

import jsonschema
import pytest

from stepdoc import Recorder, Repeat, repeat, step
from stepdoc.core.record import case_record
from tests.helpers import procedure, report

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "src/stepdoc/schema/run-record.schema.json").read_text()
)
READ_FIFO = 1


def drain(dev):
    while not dev.map.status.pll_locked:
        dev.map.ctrl.mode = READ_FIFO
        dev.map.adc.value


def test_for_loop_of_one_write_is_one_line(dev, rec):
    with step("Empty FIFO"):
        for _ in range(16):
            dev.map.ctrl.mode = READ_FIFO
    assert procedure(rec) == ["1. Empty FIFO", "   1.1. Repeat dev.map.ctrl.mode = 1"]
    assert "   1.1. Repeat dev.map.ctrl.mode = 1 -> (16 times, " in report(rec)


def test_while_loop_collapses_by_pass(dev, rec):
    with step("Empty FIFO"):
        drain(dev)
    (rep, last) = rec.steps[0].entries
    assert isinstance(rep, Repeat) and rep.count == 2 and rep.number == "1.1"
    assert [e.number for e in rep.entries] == ["1.1.1", "1.1.2", "1.1.3"]
    assert procedure(rec) == [
        "1. Empty FIFO",
        "   1.1. Repeat",
        "      1.1.1. Read dev.map.status.pll_locked",
        "      1.1.2. dev.map.ctrl.mode = 1",
        "      1.1.3. Read dev.map.adc.value",
        # The loop's last test of its condition, which ended it.
        "   1.2. Read dev.map.status.pll_locked",
    ]
    assert "   1.1. Repeat   2 times, " in report(rec)
    assert "   1.2. Read dev.map.status.pll_locked -> True" in report(rec)


def test_many_passes_and_entries_after_the_loop(dev, rec):
    dev._lock_after = 50
    with step("Empty FIFO"):
        drain(dev)
        dev.map.ctrl.enable = 0
    entries = rec.steps[0].entries
    assert [type(e).__name__ for e in entries] == ["Repeat", "Action", "Action"]
    assert entries[0].count == 49 and [e.number for e in entries] == ["1.1", "1.2", "1.3"]


def test_repeat_block_names_the_loop_and_absorbs_the_last_condition_test(dev, rec):
    with step("Empty FIFO"):
        dev.map.ctrl.enable = 1
        with repeat(until="FIFO is empty"):
            drain(dev)
        dev.map.ctrl.enable = 0
    assert procedure(rec) == [
        "1. Empty FIFO",
        "   1.1. dev.map.ctrl.enable = 1",
        "   1.2. Repeat until FIFO is empty",
        "      1.2.1. Read dev.map.status.pll_locked",
        "      1.2.2. dev.map.ctrl.mode = 1",
        "      1.2.3. Read dev.map.adc.value",
        "   1.3. dev.map.ctrl.enable = 0",
    ]
    assert "   1.2. Repeat until FIFO is empty   2 times, " in report(rec)


def test_repeat_block_keeps_its_loop_apart_from_identical_actions_around_it(dev, rec):
    with step("Fill"):
        dev.map.ctrl.mode = READ_FIFO
        with repeat(until="full"):
            for _ in range(3):
                dev.map.ctrl.mode = READ_FIFO
    before, loop = rec.steps[0].entries
    assert before.count == 1 and isinstance(loop, Repeat) and loop.count == 3
    assert loop.entries[0].count == 1


def test_repeat_block_of_zero_or_one_pass(dev, rec):
    with step("Already empty"):
        with repeat(until="FIFO is empty"):
            pass
    with step("One pass"):
        with repeat(until="FIFO is empty"):
            dev.map.ctrl.mode = READ_FIFO
    lines = report(rec).splitlines()
    assert "   1.1. Repeat until FIFO is empty   0 times" in lines
    assert "   2.1. Repeat until FIFO is empty   once" in lines
    assert procedure(rec)[1] == "   1.1. Repeat until FIFO is empty"


def test_a_failing_pass_stays_visible(dev, rec):
    with pytest.raises(AssertionError):
        with step("Empty FIFO"):
            for i in range(8):
                dev.map.ctrl.mode = READ_FIFO
                dev.map.dac.level = 1
                assert i < 5
    entries = rec.steps[0].entries
    # Passes 0-5 did the same actions (the assert of pass 5 failed after them).
    assert [type(e).__name__ for e in entries] == ["Repeat", "Check"]
    assert entries[0].count == 6 and entries[1].number == "1.2"
    assert entries[1].passed is False and rec.steps[0].status == "failed"
    assert "   1.2. Verify i < 5   ✗" in report(rec).splitlines()


def test_a_pass_that_differs_ends_the_loop(dev, rec):
    with step("Mixed"):
        for level in (1, 1, 1, 2, 1):
            dev.map.ctrl.mode = READ_FIFO
            dev.map.dac.level = level
    entries = rec.steps[0].entries
    assert [type(e).__name__ for e in entries] == ["Repeat", "Action", "Action", "Action", "Action"]
    assert entries[0].count == 3
    assert [e.value for e in entries[2:]] == [2, 1, 1]


def test_passing_checks_in_every_pass_are_part_of_the_loop(dev, rec):
    with step("Empty FIFO"):
        for _ in range(4):
            dev.map.ctrl.mode = READ_FIFO
            rec.add_check(_check("FIFO not underflowed"))
    (loop,) = rec.steps[0].entries
    assert loop.count == 4 and [type(e).__name__ for e in loop.entries] == ["Action", "Check"]
    assert procedure(rec)[-1] == "      1.1.2. Verify FIFO not underflowed"


def test_reads_feeding_a_check_in_each_pass(dev, rec):
    with step("Read back"):
        for _ in range(3):
            dev.map.ctrl.mode = READ_FIFO
            dev.map.adc.value; rec.add_check(_check("adc ok", at=sys._getframe().f_lineno))  # noqa: E702
    (loop,) = rec.steps[0].entries
    check = loop.entries[1]
    assert loop.count == 3 and [r.target for r in check.reads] == ["dev.map.adc.value"]


def test_failed_check_in_a_pass_is_not_collapsed(dev, rec):
    with step("Empty FIFO"):
        for i in range(4):
            dev.map.ctrl.mode = READ_FIFO
            rec.add_check(_check("FIFO not underflowed", passed=i != 3))
    entries = rec.steps[0].entries
    assert [type(e).__name__ for e in entries] == ["Repeat", "Action", "Check"]
    assert entries[0].count == 3 and entries[2].passed is False
    assert entries[2].number == "1.3" and rec.steps[0].status == "failed"


def test_nested_step_ends_the_loop(dev, rec):
    with step("Outer"):
        for _ in range(2):
            dev.map.ctrl.mode = READ_FIFO
            dev.map.dac.level = 1
        dev.map.ctrl.mode = READ_FIFO
        with step("Inner"):
            dev.map.ctrl.enable = 1
    entries = rec.steps[0].entries
    assert [type(e).__name__ for e in entries] == ["Repeat", "Action", "Step"]
    assert [e.number for e in entries] == ["1.1", "1.2", "1.3"]


def test_unscoped_loops_and_turning_collapse_off(dev):
    with Recorder() as rec:
        drain(dev)
    assert isinstance(rec.unscoped[0], Repeat) and rec.unscoped[0].section == "procedure"
    assert rec.unscoped[0].number is None
    with Recorder(collapse_repeats=False) as rec:
        dev._polls = 0
        drain(dev)
    assert len(rec.unscoped) == 7


def test_repeat_entries_in_the_run_record(dev, rec):
    with step("Empty FIFO"):
        with repeat(until="FIFO is empty"):
            drain(dev)
        drain(dev)
    record = case_record(rec)
    jsonschema.validate({"schema_version": "0.4", "generator": {"name": "stepdoc", "version": "x",
                         "python": "3"}, "created_at": "", "dry_run": False, "tests": [record]}, SCHEMA)
    loop = record["steps"][0]["entries"][0]
    assert loop["type"] == "repeat" and loop["until"] == "FIFO is empty" and loop["count"] == 2
    assert [e["number"] for e in loop["entries"]] == ["1.1.1", "1.1.2", "1.1.3"]
    assert loop["t_last"] >= loop["t"]


def test_repeat_outside_a_recorder_does_nothing(dev):
    with repeat(until="anything"):
        drain(dev)


# --------------------------------------------------------------------------- #


def _check(text, passed=True, at=None):
    from stepdoc import Check, Location

    loc = Location(__file__, at) if at else Location(__file__, 1)
    return Check(text=text, passed=passed, location=loc)


def test_repeat_block_whose_passes_differ_keeps_them_all(dev, rec):
    with step("Mixed"):
        with repeat(until="done"):
            for level in (1, 2, 3):
                dev.map.dac.level = level
    (loop,) = rec.steps[0].entries
    assert loop.count is None and [a.value for a in loop.entries] == [1, 2, 3]
    assert [a.number for a in loop.entries] == ["1.1.1", "1.1.2", "1.1.3"]
    assert "   1.1. Repeat until done" in report(rec).splitlines()


FIFO_DRIVER = '''
class Fifo:
    def __init__(self, depth):
        self.items = list(range(depth))
        self.hooks = []

    def _event(self, reg, op, value):
        for hook in self.hooks:
            hook(reg, op, value)

    @property
    def empty(self):
        value = not self.items
        self._event("dev.fifo.status.empty", "read", value)
        return value

    def read_fifo(self, read_command=0x5A):
        self._event("dev.fifo.fifo_data", "write", read_command)
        return self.items.pop(0)
'''

FIFO_TESTS = '''
import pytest
import stepdoc
from stepdoc import repeat, step
from fifodev import Fifo

FIFO_DEPTH = 16


@pytest.fixture
def dev():
    d = Fifo(FIFO_DEPTH)
    d.hooks.append(lambda reg, op, value: stepdoc.record_action(reg, op, value, value_from=("read_command",)))
    return d


def test_for_loop(dev):
    with step("Empty FIFO"):
        for _ in range(FIFO_DEPTH):
            word = dev.read_fifo()
            assert word >= 0


def test_while_loop(dev):
    with step("Empty FIFO"):
        with repeat(until="FIFO is empty"):
            while not dev.empty:
                dev.read_fifo()
'''


def test_fifo_loops_through_the_plugin(pytester):
    pytester.makepyfile(fifodev=FIFO_DRIVER, test_fifo=FIFO_TESTS)
    pytester.makeini("[pytest]\nenable_assertion_pass_hook = true\n")
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "--stepdoc-record=out/run.json",
        "--stepdoc-procedure=out/procedure.txt", "--stepdoc-report=out/report.html",
    )
    result.assert_outcomes(passed=2)
    record = json.loads((pytester.path / "out/run.json").read_text())
    jsonschema.validate(record, SCHEMA)
    text = (pytester.path / "out/procedure.txt").read_text()
    assert "   1.1. Repeat\n" in text
    assert "      1.1.1. dev.fifo.fifo_data = " in text
    assert "      1.1.2. Verify word >= 0\n" in text
    assert "   1.1. Repeat until FIFO is empty\n" in text
    assert "      1.1.1. Read dev.fifo.status.empty\n" in text
    first, second = (t["steps"][0]["entries"] for t in record["tests"])
    assert [e["type"] for e in first] == ["repeat"] and first[0]["count"] == 16
    assert [e["type"] for e in second] == ["repeat"] and second[0]["count"] == 16
    assert "Repeat until FIFO is empty" in (pytester.path / "out/report.html").read_text()


def test_repeat_block_with_a_title(dev, rec):
    with step("Empty FIFO"):
        with repeat("Drain the FIFO", until="FIFO is empty"):
            drain(dev)
        with repeat("Read back", until="done"):
            pass
        with repeat("Pulse enable"):
            for _ in range(3):
                dev.map.ctrl.enable = 1
    assert [line for line in procedure(rec) if "repeat" in line] == [
        "   1.1. Drain the FIFO (repeat until FIFO is empty)",
        "   1.2. Read back (repeat until done)",
        "   1.3. Pulse enable (repeat)",
    ]
    lines = report(rec).splitlines()
    # A titled block is a step: status mark, then how often.
    assert any(line.startswith("   1.1. Drain the FIFO (repeat until FIFO is empty)   ✓ 2 times") for line in lines)
    assert "   1.3. Pulse enable (repeat)   ✓ 3 times" in report(rec)
    record = case_record(rec)
    first, second, third = record["steps"][0]["entries"]
    assert first["type"] == "step" and first["title"] == "Drain the FIFO"
    assert first["loop"]["until"] == "FIFO is empty" and first["loop"]["count"] == 2
    assert second["loop"] == {"until": "done", "count": 0, "t_last": None}
    assert third["title"] == "Pulse enable" and third["loop"]["until"] is None
    jsonschema.validate({"schema_version": "0.4", "generator": {"name": "stepdoc", "version": "x",
                         "python": "3"}, "created_at": "", "dry_run": False, "tests": [record]}, SCHEMA)


def test_until_is_keyword_only():
    with pytest.raises(TypeError):
        repeat("Drain the FIFO", "FIFO is empty")
