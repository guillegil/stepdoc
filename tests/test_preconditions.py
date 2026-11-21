"""PRE-1…3: ``stepdoc.precondition`` in the core, the record, the documents and
the pytest plugin (shared fixtures: "already established")."""

import asyncio
import contextlib
import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest

import stepdoc
from stepdoc import Recorder, attach_check, precondition, record_action, step
from stepdoc.core.model import Location, Step
from stepdoc.core.record import case_record, run_record
from stepdoc.renderers import render
from stepdoc.renderers.common import procedure_lines, procedures, report_lines
from tests.helpers import procedure, report
from tests.test_adapters import FAKE_LOADER, FAKE_VERIFY, VERIFY_SPECS

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "src/stepdoc/schema/run-record.schema.json").read_text()
)


def write(target, value=1):
    record_action(target, "write", value, value_from=("value",))


def outline(rec):
    """(section, number, title) of the top-level steps, in the record's order."""
    return [(s.section, s.number, s.title) for s in rec.steps]


def numbers(s):
    return [e.number for e in s.entries]


# The block (PRE-1) --------------------------------------------------------- #


def test_block_is_a_step_in_its_own_section():
    with Recorder() as rec:
        with precondition("System Mode A") as p:
            write("sys.mode")
            attach_check(True, "mode is A")
            with step("Wait for lock"):
                write("pll.enable")
        with step("Measure"):
            write("adc.start")
    mode, measure = rec.steps
    assert p is mode
    assert (mode.section, mode.number, mode.title) == ("precondition", "P1", "System Mode A")
    assert numbers(mode) == ["P1.1", "P1.2", "P1.3"]
    lock = mode.children[0]
    assert (lock.section, lock.number) == ("precondition", "P1.3")
    assert lock.actions[0].number == "P1.3.1"
    assert mode.status == "passed" and mode.ended is not None and mode.error is None
    assert mode.location.file.endswith("test_preconditions.py")
    # It takes no number from the procedure, and nothing is left outside a step.
    assert (measure.section, measure.number) == ("procedure", "1")
    assert rec.unscoped == []
    assert rec.status == "passed"


def test_is_exported_at_the_top_level():
    assert stepdoc.precondition is precondition
    assert "precondition" in stepdoc.__all__


def test_two_preconditions_are_p1_and_p2():
    with Recorder() as rec:
        with precondition("Supply on"):
            write("psu.out")
        with precondition("System Mode A"):
            write("sys.mode")
    assert outline(rec) == [("precondition", "P1", "Supply on"), ("precondition", "P2", "System Mode A")]
    assert [numbers(s) for s in rec.steps] == [["P1.1"], ["P2.1"]]


@pytest.mark.parametrize("section", ["setup", "procedure", "teardown"])
def test_section_does_not_follow_the_phase(section):
    with Recorder() as rec:
        rec.section = section
        with precondition("System Mode A"):
            write("sys.mode")
        with step("Other"):
            pass
        write("loose")
    prefix = {"setup": "S", "procedure": "", "teardown": "T"}[section]
    assert outline(rec) == [("precondition", "P1", "System Mode A"), (section, f"{prefix}1", "Other")]
    assert [a.section for a in rec.unscoped] == [section]


def test_block_inside_another_precondition_is_nested():
    with Recorder() as rec:
        with precondition("System Mode A"):
            write("sys.mode")
            with precondition("Clock locked") as inner:
                write("pll.enable")
            write("sys.go")
    (mode,) = rec.steps
    assert numbers(mode) == ["P1.1", "P1.2", "P1.3"]
    assert mode.children == [inner]
    assert (inner.section, inner.number, numbers(inner)) == ("precondition", "P1.2", ["P1.2.1"])


def test_block_inside_a_step_of_a_precondition_is_nested():
    with Recorder() as rec:
        with precondition("System Mode A"):
            with step("Bring up"):
                with precondition("Clock locked"):
                    write("pll.enable")
    assert [s.number for s in rec.steps] == ["P1"]
    inner = rec.steps[0].children[0].children[0]
    assert (inner.section, inner.number, inner.title) == ("precondition", "P1.1.1", "Clock locked")


# Never part of another step (PRE-2) ---------------------------------------- #


def test_block_inside_a_setup_step_is_lifted_out():
    with Recorder() as rec:
        rec.section = "setup"
        with step("Bring up the bench") as bench:
            write("bench.power")
            with precondition("System Mode A") as mode:
                assert stepdoc.current_step() is mode
                write("sys.mode")
            assert stepdoc.current_step() is bench
            write("bench.fan")
            attach_check(True, "fan runs")
    assert sorted(outline(rec)) == [
        ("precondition", "P1", "System Mode A"),
        ("setup", "S1", "Bring up the bench"),
    ]
    # The step holds what it did itself, numbered as if the block were elsewhere.
    assert bench.children == []
    assert numbers(bench) == ["S1.1", "S1.2", "S1.3"]
    assert [a.target for a in bench.actions] == ["bench.power", "bench.fan"]
    assert numbers(mode) == ["P1.1"] and mode.actions[0].target == "sys.mode"
    assert bench.status == mode.status == "passed"
    text = procedure(rec)
    assert text.index("Pre-conditions") < text.index("P1. System Mode A") < text.index("Setup")
    assert text[text.index("Setup") + 1 :] == [
        "S1. Bring up the bench",
        "   S1.1. bench.power = 1",
        "   S1.2. bench.fan = 1",
        "   S1.3. Verify fan runs",
    ]


def test_block_inside_a_procedure_step_is_lifted_out():
    with Recorder() as rec:
        with step("Outer"):
            with step("Inner") as inner:
                with precondition("System Mode A"):
                    write("sys.mode")
                    with precondition("Clock locked"):
                        write("pll.enable")
                write("after")
        with precondition("Second"):
            pass
    assert sorted(outline(rec)) == [
        ("precondition", "P1", "System Mode A"),
        ("precondition", "P2", "Second"),
        ("procedure", "1", "Outer"),
    ]
    assert numbers(inner) == ["1.1.1"]
    mode = next(s for s in rec.steps if s.number == "P1")
    assert numbers(mode) == ["P1.1", "P1.2"] and mode.children[0].title == "Clock locked"


def test_a_failure_in_a_lifted_block_fails_both_steps():
    with Recorder() as rec:
        with pytest.raises(AssertionError):
            with step("Bring up"):
                with precondition("System Mode A"):
                    assert 1 == 2, "mode not reached"
    by_number = {s.number: s for s in rec.steps}
    assert by_number["P1"].status == "failed" and by_number["1"].status == "failed"
    assert [c.text for c in by_number["P1"].checks] == ["1 == 2"]


def test_same_action_around_a_lifted_block_is_not_a_repeat():
    """ACT-4 collapses the same event from the same line; a pre-condition between
    two of them separates them, though it is not an entry of their step."""
    with Recorder():
        with step("Control: the same write twice") as control:
            for _ in range(2):
                write("ctrl.strobe")
        with step("Strobe around the mode change") as around:
            for i in range(2):
                write("ctrl.strobe")
                if i == 0:
                    with precondition("System Mode A"):
                        write("sys.mode")
            for _ in range(2):
                write("ctrl.strobe")
    assert [a.count for a in control.actions] == [2]
    # Before and after the pre-condition are two lines; after it, repeats collapse again.
    assert [(a.number, a.target, a.count) for a in around.actions] == [
        ("2.1", "ctrl.strobe", 1),
        ("2.2", "ctrl.strobe", 1),
        ("2.3", "ctrl.strobe", 2),
    ]
    assert around.children == []


def test_same_unscoped_action_around_a_precondition_is_not_a_repeat():
    with Recorder() as rec:
        for i in range(2):
            write("ctrl.strobe")
            if i == 0:
                with precondition("System Mode A"):
                    write("sys.mode")
    assert [a.count for a in rec.unscoped] == [1, 1]
    with Recorder() as control:
        for _ in range(2):
            write("ctrl.strobe")
    assert [a.count for a in control.unscoped] == [2]


def test_same_action_around_a_decorated_precondition_is_not_a_repeat():
    @precondition("System Mode A")
    def mode_a():
        write("sys.mode")

    with Recorder() as rec:
        with step("Strobe around the mode change") as around:
            for i in range(3):
                write("ctrl.strobe")
                if i == 1:
                    mode_a()
    assert [(a.number, a.count) for a in around.actions] == [("1.1", 2), ("1.2", 1)]
    assert [s.number for s in rec.steps if s.section == "precondition"] == ["P1"]


def test_same_action_around_a_nested_precondition_stays_in_order():
    with Recorder():
        with precondition("System Mode A") as mode:
            for i in range(2):
                write("ctrl.strobe")
                if i == 0:
                    with precondition("Clock locked"):
                        pass
    assert numbers(mode) == ["P1.1", "P1.2", "P1.3"]
    assert [type(e).__name__ for e in mode.entries] == ["Action", "Step", "Action"]


# Status and errors --------------------------------------------------------- #


def test_failing_assert_fails_the_precondition_and_is_reraised():
    with Recorder() as rec:
        with pytest.raises(AssertionError, match="mode not reached"):
            with precondition("System Mode A"):
                write("sys.mode")
                assert 1 == 2, "mode not reached"
    (mode,) = rec.steps
    assert mode.status == "failed" and rec.status == "failed"
    assert mode.error.startswith("AssertionError")
    (check,) = mode.checks
    assert (check.text, check.passed, check.number) == ("1 == 2", False, "P1.2")
    assert check.detail.startswith("mode not reached")


def test_exception_is_an_error_and_is_reraised():
    with Recorder() as rec:
        with pytest.raises(ZeroDivisionError):
            with precondition("System Mode A"):
                1 / 0
        with precondition("Still recorded"):
            pass
    first, second = rec.steps
    assert first.status == "error" and first.error.startswith("ZeroDivisionError")
    assert first.ended is not None
    assert (second.number, second.status) == ("P2", "passed")
    assert rec.status == "error"


def test_failed_check_fails_the_precondition():
    with Recorder() as rec:
        with precondition("System Mode A"):
            attach_check(False, "mode is A")
        with precondition("Child fails"):
            with step("Inner"):
                attach_check(False, "bad")
    assert [s.status for s in rec.steps] == ["failed", "failed"]


def test_dry_run_records_without_judging():
    with Recorder(dry_run=True) as rec:
        with precondition("System Mode A"):
            write("sys.mode")
            attach_check(False, "would fail")
            assert False, "simulated value is off"
        with precondition("Still documented"):
            write("more")
    first, second = rec.steps
    assert (first.number, second.number) == ("P1", "P2")
    assert [c.passed for c in first.checks] == [None, None]
    assert first.status == second.status == "unknown"


def test_dry_run_does_not_swallow_errors():
    with Recorder(dry_run=True) as rec:
        with pytest.raises(RuntimeError):
            with precondition("Broken"):
                raise RuntimeError("backend down")
    assert rec.steps[0].error == "RuntimeError: backend down"


def test_no_recorder_is_a_no_op():
    with precondition("Nothing") as p:
        assert p is None
        assert attach_check(True) is None
    with pytest.raises(KeyError):
        with precondition("Raises all the same"):
            raise KeyError("k")

    @precondition("Helper")
    def helper(x):
        return x + 1

    assert helper(1) == 2


def test_note_is_kept_and_shown():
    with Recorder() as rec:
        with precondition("System Mode A", note="Both devices share the reference clock."):
            write("sys.mode")
        with precondition(
            "Triple quoted",
            note="""
            First line.
            Second line.
            """,
        ):
            pass
        with precondition("Blank note", note="   "):
            pass
    assert [s.note for s in rec.steps] == [
        "Both devices share the reference clock.",
        "First line.\nSecond line.",
        None,
    ]
    assert procedure(rec)[:3] == [
        "Pre-conditions",
        "P1. System Mode A",
        "   Note: Both devices share the reference clock.",
    ]
    assert "   Note: Both devices share the reference clock." in report(rec).splitlines()
    steps = {s["number"]: s for s in case_record(rec)["steps"]}
    assert steps["P1"]["note"] == "Both devices share the reference clock."
    assert "note" not in steps["P3"]


@pytest.mark.parametrize("title", [None, 1, b"bytes", ["Mode A"], lambda: None])
def test_title_must_be_a_str(title):
    with pytest.raises(TypeError, match="title must be a str"):
        precondition(title)
    with Recorder() as rec:
        with pytest.raises(TypeError):
            precondition(title)
    assert rec.steps == []


def test_note_must_be_a_str():
    with pytest.raises(TypeError, match="note must be a str"):
        precondition("System Mode A", note=3)
    with pytest.raises(TypeError):
        precondition("System Mode A", "a positional note")  # note is keyword-only


def test_listeners_follow_the_precondition():
    events = []
    with Recorder() as rec:
        rec.listeners.append(lambda event, s: events.append((event, s.number, s.section)))
        with step("Around"):
            with precondition("System Mode A"):
                step("Leaf")
    assert events == [
        ("started", "1", "procedure"),
        ("started", "P1", "precondition"),
        ("started", "P1.1", "precondition"),
        ("finished", "P1.1", "precondition"),
        ("finished", "P1", "precondition"),
        ("finished", "1", "procedure"),
    ]


def test_one_object_can_open_several_blocks():
    mode = precondition("System Mode A")
    with Recorder() as rec:
        with mode:
            write("sys.mode")
        with mode:
            with mode:  # nested in itself
                write("sys.mode")
    assert [(s.number, s.title) for s in rec.steps] == [("P1", "System Mode A"), ("P2", "System Mode A")]
    assert rec.steps[1].children[0].number == "P2.1"


def test_precondition_object_alone_records_nothing():
    with Recorder() as rec:
        precondition("Kept for later")
    assert rec.steps == []


# The decorator ------------------------------------------------------------- #


def test_decorator_wraps_the_whole_function():
    @precondition("System Mode A", note="Shared")
    def mode_a(value, *, extra=0):
        """Docstring kept."""
        write("sys.mode", value)
        return value + extra

    assert (mode_a.__name__, mode_a.__doc__) == ("mode_a", "Docstring kept.")
    with Recorder() as rec:
        assert mode_a(1, extra=2) == 3
        write("after")
        assert mode_a(5) == 5
    first, second = rec.steps
    assert outline(rec) == [("precondition", "P1", "System Mode A"), ("precondition", "P2", "System Mode A")]
    assert (first.note, first.status, numbers(first)) == ("Shared", "passed", ["P1.1"])
    assert first.actions[0].value == 1 and second.actions[0].value == 5
    assert len(rec.unscoped) == 1
    # Located at the function, not at the call.
    assert first.location.file.endswith("test_preconditions.py")
    assert first.location == second.location
    assert first.location.line < test_decorator_wraps_the_whole_function.__code__.co_firstlineno + 10


def test_decorated_function_called_inside_a_step_is_lifted_out():
    @precondition("System Mode A")
    def mode_a():
        write("sys.mode")

    @precondition("Everything ready")
    def ready():
        mode_a()

    with Recorder() as rec:
        with step("Bring up") as bring_up:
            write("bench.power")
            mode_a()
            write("bench.fan")
        ready()
    assert sorted(outline(rec)) == [
        ("precondition", "P1", "System Mode A"),
        ("precondition", "P2", "Everything ready"),
        ("procedure", "1", "Bring up"),
    ]
    assert numbers(bring_up) == ["1.1", "1.2"]
    nested = next(s for s in rec.steps if s.number == "P2").children[0]
    assert (nested.number, nested.title) == ("P2.1", "System Mode A")


def test_decorated_function_failure_and_error():
    @precondition("Asserts")
    def asserts():
        assert 1 == 2, "values differ"

    @precondition("Raises")
    def raises():
        raise RuntimeError("backend down")

    with Recorder() as rec:
        with pytest.raises(AssertionError, match="values differ"):
            asserts()
        with pytest.raises(RuntimeError, match="backend down"):
            raises()
    first, second = rec.steps
    assert (first.number, first.status, first.checks[0].text) == ("P1", "failed", "1 == 2")
    assert (second.number, second.status, second.error) == ("P2", "error", "RuntimeError: backend down")
    assert stepdoc.current_step() is None


def test_decorated_function_in_dry_run():
    @precondition("Asserts")
    def asserts():
        assert 1 == 2
        return "unreached"

    @precondition("Raises")
    def raises():
        raise RuntimeError("backend down")

    with Recorder(dry_run=True) as rec:
        assert asserts() is None  # recorded, not judged; the test goes on
        with pytest.raises(RuntimeError):
            raises()
    assert [s.status for s in rec.steps] == ["unknown", "unknown"]
    assert rec.steps[0].checks[0].passed is None


def test_decorator_on_a_generator_function_covers_the_part_before_the_yield():
    @precondition("System Mode A")
    def mode_a(value):
        write("sys.mode", value)
        with step("Wait for lock"):
            write("pll.enable")
        yield "resource"
        write("sys.mode", 0)
        with step("Leave mode A"):
            write("pll.enable", 0)

    with Recorder() as rec:
        rec.section = "setup"
        gen = mode_a(1)
        assert rec.steps == []  # nothing runs before the first next()
        assert next(gen) == "resource"
        (mode,) = rec.steps
        assert mode.ended is not None and mode.status == "passed"  # closed at the yield
        assert stepdoc.current_step() is None
        rec.section = "teardown"
        with pytest.raises(StopIteration):
            next(gen)
    assert outline(rec) == [("precondition", "P1", "System Mode A"), ("teardown", "T1", "Leave mode A")]
    assert numbers(mode) == ["P1.1", "P1.2"]
    (after,) = rec.unscoped
    assert (after.section, after.value) == ("teardown", 0)
    assert mode.location.file.endswith("test_preconditions.py")


def test_decorated_generator_that_fails_before_the_yield():
    @precondition("Asserts")
    def asserts():
        assert 1 == 2, "values differ"
        yield "unreached"

    @precondition("Raises")
    def raises():
        raise RuntimeError("backend down")
        yield

    @precondition("Returns")
    def returns():
        write("only")
        return "done"
        yield

    with Recorder() as rec:
        with pytest.raises(AssertionError, match="values differ"):
            next(asserts())
        with pytest.raises(RuntimeError):
            next(raises())
        with pytest.raises(StopIteration) as stop:
            next(returns())
    assert stop.value.value == "done"
    assert [(s.number, s.status) for s in rec.steps] == [("P1", "failed"), ("P2", "error"), ("P3", "passed")]
    assert stepdoc.current_step() is None

    with Recorder(dry_run=True) as rec:
        gen = asserts()
        assert next(gen) is None  # dry-run: the fixture still yields, the test goes on
        with pytest.raises(StopIteration):
            next(gen)
        with pytest.raises(RuntimeError):
            next(raises())
    assert [s.status for s in rec.steps] == ["unknown", "unknown"]


def _yields_once(log):
    @precondition("System Mode A")
    def mode_a():
        write("sys.mode")
        try:
            got = yield "first"
            log.append(("sent", got))
        except KeyError as exc:
            log.append(("thrown", exc.args[0]))
        finally:
            log.append("finally")
        write("sys.mode", 0)
        return "result"

    return mode_a


def test_decorated_generator_send():
    log = []
    with Recorder() as rec:
        gen = _yields_once(log)()
        assert next(gen) == "first"
        with pytest.raises(StopIteration) as stop:
            gen.send("value")
    assert stop.value.value == "result"
    assert log == [("sent", "value"), "finally"]
    assert numbers(rec.steps[0]) == ["P1.1"] and len(rec.unscoped) == 1


def test_decorated_generator_throw():
    log = []
    with Recorder() as rec:
        gen = _yields_once(log)()
        next(gen)
        with pytest.raises(StopIteration) as stop:
            gen.throw(KeyError("lost"))  # handled inside; the generator ends
        assert stop.value.value == "result"
        assert log == [("thrown", "lost"), "finally"]

        log.clear()
        gen = _yields_once(log)()
        next(gen)
        with pytest.raises(ValueError, match="not handled"):
            gen.throw(ValueError("not handled"))
        assert log == ["finally"]
    # What happens after the yield does not change the pre-condition.
    assert [(s.number, s.status, s.error) for s in rec.steps] == [("P1", "passed", None), ("P2", "passed", None)]


def test_decorated_generator_close():
    log = []
    with Recorder() as rec:
        gen = _yields_once(log)()
        next(gen)
        gen.close()
        assert log == ["finally"]
        log.clear()
        unstarted = _yields_once(log)()
        unstarted.close()
        assert log == []
    assert len(rec.steps) == 1 and rec.steps[0].status == "passed"


def test_decorated_generator_as_a_context_manager():
    @contextlib.contextmanager
    @precondition("System Mode A")
    def mode_a():
        write("sys.mode")
        yield "A"
        write("sys.mode", 0)

    with Recorder() as rec:
        with mode_a() as value:
            with step("Measure"):
                write("adc.start")
        with pytest.raises(ZeroDivisionError):
            with mode_a():
                1 / 0
    assert value == "A"
    assert outline(rec) == [
        ("precondition", "P1", "System Mode A"),
        ("procedure", "1", "Measure"),
        ("precondition", "P2", "System Mode A"),
    ]
    assert [s.status for s in rec.steps] == ["passed", "passed", "passed"]
    assert numbers(rec.steps[0]) == ["P1.1"]


def _yields_three_times(log):
    @precondition("System Mode A")
    def mode_a():
        try:
            log.append(("first", (yield 1)))
            try:
                log.append(("second", (yield 2)))
            except KeyError as exc:
                log.append(("thrown", exc.args[0]))
            log.append(("third", (yield 3)))
        finally:
            log.append("finally")

    return mode_a


def test_decorated_generator_send_after_the_second_yield():
    log = []
    with Recorder():
        gen = _yields_three_times(log)()
        assert [next(gen), gen.send("a"), gen.send("b")] == [1, 2, 3]
        with pytest.raises(StopIteration):
            gen.send("c")
    assert log == [("first", "a"), ("second", "b"), ("third", "c"), "finally"]


def test_decorated_generator_throw_at_the_second_yield():
    log = []
    with Recorder():
        gen = _yields_three_times(log)()
        assert [next(gen), gen.send("a")] == [1, 2]
        assert gen.throw(KeyError("lost")) == 3
    assert log == [("first", "a"), ("thrown", "lost")]


def test_decorated_generator_close_at_the_second_yield():
    log = []
    with Recorder():
        gen = _yields_three_times(log)()
        assert [next(gen), gen.send("a")] == [1, 2]
        gen.close()
        assert log == [("first", "a"), "finally"]  # `gen` is still referenced: no help from gc


def test_decorator_on_a_coroutine_function():
    @precondition("System Mode A")
    async def mode_a(value):
        write("sys.mode", value)
        await asyncio.sleep(0)
        with step("Wait for lock"):
            write("pll.enable")
        return value * 2

    @precondition("Asserts")
    async def asserts():
        await asyncio.sleep(0)
        assert 1 == 2, "values differ"

    @precondition("Raises")
    async def raises():
        raise RuntimeError("backend down")

    assert asyncio.iscoroutinefunction(mode_a) and mode_a.__name__ == "mode_a"

    async def scenario():
        with Recorder() as rec:
            with step("Bring up") as bring_up:
                assert await mode_a(2) == 4
                write("after")
            with pytest.raises(AssertionError, match="values differ"):
                await asserts()
            with pytest.raises(RuntimeError):
                await raises()
        with Recorder(dry_run=True) as dry:
            assert await asserts() is None
            with pytest.raises(RuntimeError):
                await raises()
        return rec, bring_up, dry

    rec, bring_up, dry = asyncio.run(scenario())
    by_number = {s.number: s for s in rec.steps}
    assert sorted(by_number) == ["1", "P1", "P2", "P3"]
    assert numbers(by_number["P1"]) == ["P1.1", "P1.2"] and numbers(bring_up) == ["1.1"]
    assert [by_number[n].status for n in ("P1", "P2", "P3")] == ["passed", "failed", "error"]
    assert by_number["P1"].location.file.endswith("test_preconditions.py")
    assert [s.status for s in dry.steps] == ["unknown", "unknown"]


def _async_yields(log):
    @precondition("System Mode A")
    async def mode_a():
        write("sys.mode")
        await asyncio.sleep(0)
        try:
            got = yield "first"
            log.append(("sent", got))
            write("sys.mode", 0)
            with step("Leave mode A"):
                pass
        except KeyError as exc:
            log.append(("thrown", exc.args[0]))
        finally:
            log.append("finally")

    return mode_a


def test_decorator_on_an_async_generator_covers_the_part_before_the_yield():
    log = []

    @precondition("Asserts")
    async def asserts():
        assert 1 == 2, "values differ"
        yield "unreached"

    @precondition("Raises")
    async def raises():
        raise RuntimeError("backend down")
        yield

    @precondition("Empty")
    async def empty():
        write("only")
        return
        yield

    async def scenario():
        with Recorder() as rec:
            rec.section = "setup"
            gen = _async_yields(log)()
            assert rec.steps == []
            assert await gen.__anext__() == "first"
            (mode,) = rec.steps
            assert mode.ended is not None and mode.status == "passed"
            assert stepdoc.current_step() is None
            rec.section = "teardown"
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()
            with pytest.raises(AssertionError, match="values differ"):
                await asserts().__anext__()
            with pytest.raises(RuntimeError):
                await raises().__anext__()
            with pytest.raises(StopAsyncIteration):
                await empty().__anext__()
        with Recorder(dry_run=True) as dry:
            gen = asserts()
            assert await gen.__anext__() is None
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()
            with pytest.raises(RuntimeError):
                await raises().__anext__()
        return rec, dry

    rec, dry = asyncio.run(scenario())
    assert log == [("sent", None), "finally"]
    assert outline(rec) == [
        ("precondition", "P1", "System Mode A"),
        ("teardown", "T1", "Leave mode A"),
        ("precondition", "P2", "Asserts"),
        ("precondition", "P3", "Raises"),
        ("precondition", "P4", "Empty"),
    ]
    assert numbers(rec.steps[0]) == ["P1.1"]
    assert [a.section for a in rec.unscoped] == ["teardown"]
    assert [s.status for s in rec.steps] == ["passed", "passed", "failed", "error", "passed"]
    assert [s.status for s in dry.steps] == ["unknown", "unknown"]


def test_decorator_keeps_an_async_generator_an_async_generator():
    import inspect

    assert inspect.isasyncgenfunction(_async_yields([]))
    assert inspect.isgeneratorfunction(_yields_once([]))


def test_decorated_async_generator_asend():
    log = []

    async def scenario():
        with Recorder():
            gen = _async_yields(log)()
            await gen.__anext__()
            with pytest.raises(StopAsyncIteration):
                await gen.asend("value")

    asyncio.run(scenario())
    assert log == [("sent", "value"), "finally"]


def test_decorated_async_generator_athrow():
    log = []

    async def scenario():
        with Recorder():
            gen = _async_yields(log)()
            await gen.__anext__()
            with pytest.raises(StopAsyncIteration):
                await gen.athrow(KeyError("lost"))
            return list(log)

    assert asyncio.run(scenario()) == [("thrown", "lost"), "finally"]


def test_decorated_async_generator_aclose():
    log = []

    async def scenario():
        with Recorder():
            gen = _async_yields(log)()
            await gen.__anext__()
            await gen.aclose()
            return list(log)  # `gen` is still referenced: no help from gc

    assert asyncio.run(scenario()) == ["finally"]


# The record ---------------------------------------------------------------- #


def reference(original, test_id, number="P1"):
    """What the plugin adds to a test that found ``original`` already established."""
    return Step(
        number=number,
        title=original.title,
        section="precondition",
        status=original.status,
        location=original.location,
        note=original.note,
        applied_in=test_id,
        origin=original,
    )


def make_run():
    """Two cases of one procedure: the first applies System Mode A, the second
    finds it established; and a test whose pre-condition failed."""
    cases = []
    with Recorder() as rec:
        rec.section = "setup"
        with precondition("System Mode A", note="Shared by every mode A test") as mode:
            write("sys.mode", 3)
            attach_check(True, "mode is A")
        with step("Configure"):
            write("ctrl.gain", 2)
        rec.section = "procedure"
        with step("Measure"):
            write("adc.start")
        rec.section = "teardown"
        with step("Restore"):
            write("ctrl.gain", 0)
    cases.append(case_record(rec, test_id="t.py::test_mode[1]", procedure_id="t.py::test_mode", case="1",
                             params={"n": 1}, outcome="passed"))
    with Recorder() as rec:
        rec.steps.append(reference(mode, "t.py::test_mode[1]"))
        rec.section = "setup"
        with step("Configure"):
            write("ctrl.gain", 2)
        rec.section = "procedure"
        with step("Measure"):
            write("adc.start")
    cases.append(case_record(rec, test_id="t.py::test_mode[2]", procedure_id="t.py::test_mode", case="2",
                             params={"n": 2}, outcome="passed"))
    with Recorder() as rec:
        with pytest.raises(AssertionError):
            with precondition("System Mode <B>"):
                write("sys.mode", 4)
                assert 1 == 2, "mode B not reached"
    cases.append(case_record(rec, test_id="t.py::test_broken", outcome="failed"))
    return run_record(cases)


def test_record_validates_and_carries_section_and_applied_in():
    record = make_run()
    jsonschema.validate(record, SCHEMA)
    assert record["schema_version"] == "0.5"
    json.dumps(record)
    applied, established, broken = record["tests"]

    p1 = applied["steps"][0]
    assert (p1["type"], p1["section"], p1["number"], p1["title"]) == ("step", "precondition", "P1", "System Mode A")
    assert "applied_in" not in p1 and "origin" not in p1
    assert [(e["type"], e["number"]) for e in p1["entries"]] == [("action", "P1.1"), ("check", "P1.2")]
    assert p1["location"]["file"].endswith("test_preconditions.py")
    assert [s["section"] for s in applied["steps"]] == ["precondition", "setup", "procedure", "teardown"]

    ref = established["steps"][0]
    assert ref["applied_in"] == "t.py::test_mode[1]"
    assert (ref["section"], ref["number"], ref["title"], ref["status"]) == ("precondition", "P1", "System Mode A", "passed")
    assert ref["entries"] == [] and ref["note"] == "Shared by every mode A test"
    assert "origin" not in ref
    assert established["status"] == "passed"

    failed = broken["steps"][0]
    assert (failed["section"], failed["status"]) == ("precondition", "failed")
    assert failed["error"].startswith("AssertionError")
    assert broken["status"] == "failed"


def test_schema_rejects_an_unknown_section_and_a_non_string_applied_in():
    record = make_run()
    record["tests"][0]["steps"][0]["section"] = "postcondition"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(record, SCHEMA)
    record = make_run()
    record["tests"][1]["steps"][0]["applied_in"] = None
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(record, SCHEMA)


def test_record_without_preconditions_has_no_such_section():
    with Recorder() as rec:
        with step("Plain"):
            pass
    case = case_record(rec)
    assert [s["section"] for s in case["steps"]] == ["procedure"]
    assert all("applied_in" not in s for s in case["steps"])
    assert "Pre-conditions" not in procedure(rec) and "Pre-conditions" not in report(rec)


def test_reference_step_copies_but_does_not_compare_origin():
    with Recorder():
        with precondition("System Mode A") as mode:
            pass
    ref = reference(mode, "t.py::test_a")
    assert ref.origin is mode and ref.entries == [] and ref.status == mode.status
    assert "origin" not in repr(ref)
    assert isinstance(ref.location, Location)


# The documents ------------------------------------------------------------- #

ESTABLISHED = "already established, applied in t.py::test_mode[1]"


def test_helpers_show_the_section_before_setup():
    with Recorder() as rec:
        rec.section = "setup"
        with step("Configure"):
            write("ctrl.gain")
        with precondition("System Mode A"):
            write("sys.mode")
        rec.section = "procedure"
        with step("Measure"):
            pass
    assert procedure(rec) == [
        "Pre-conditions",
        "P1. System Mode A",
        "   P1.1. sys.mode = 1",
        "Setup",
        "S1. Configure",
        "   S1.1. ctrl.gain = 1",
        "Procedure",
        "1. Measure",
    ]
    assert report(rec).splitlines()[:2] == ["Pre-conditions", "P1. System Mode A   ✓"]


def test_procedure_alone_with_a_precondition_gets_its_heading():
    with Recorder() as rec:
        with precondition("System Mode A"):
            pass
        with step("Measure"):
            pass
    assert procedure(rec) == ["Pre-conditions", "P1. System Mode A", "Procedure", "1. Measure"]


def test_text_procedure_and_report():
    record = make_run()
    text = render(record, "procedure", "text").splitlines()
    start = text.index("t.py::test_mode")
    pre, setup, proc, teardown = (text.index(h, start) for h in ("Pre-conditions", "Setup", "Procedure", "Teardown"))
    assert start < pre < setup < proc < teardown
    # The procedure shows the case that applied the pre-condition, in full.
    assert text[pre:setup] == [
        "Pre-conditions",
        "P1. System Mode A",
        "   Note: Shared by every mode A test",
        "   P1.1. sys.mode = 3",
        "   P1.2. Verify mode is A",
    ]
    assert not any("already established" in line for line in text)
    assert "P1. System Mode <B>" in text

    lines = render(record, "report", "text").splitlines()
    second = lines.index("t.py::test_mode[2]   PASSED")
    first = lines.index("t.py::test_mode[1]   PASSED")
    assert lines.index("Pre-conditions", first) < lines.index("Setup", first) < second
    assert "   P1.1. sys.mode = 3" in lines[first:second]
    after = lines[second:]
    assert after.index("Pre-conditions") < after.index("Setup") < after.index("Procedure")
    assert after[after.index("Pre-conditions") + 1] == f"P1. System Mode A   ✓ {ESTABLISHED}"
    assert after[after.index("Pre-conditions") + 2] == "   Note: Shared by every mode A test"
    assert after[after.index("Pre-conditions") + 3] == "Setup"
    assert any(line.startswith("P1. System Mode <B>   ✗") for line in lines)


def test_text_procedure_of_a_test_that_only_found_it_established():
    record = make_run()
    record["tests"] = [record["tests"][1]]
    text = render(record, "procedure", "text").splitlines()
    pre = text.index("Pre-conditions")
    assert text[pre : pre + 4] == [
        "Pre-conditions",
        f"P1. System Mode A   {ESTABLISHED}",
        "   Note: Shared by every mode A test",
        "Setup",
    ]
    assert not any(line.lstrip().startswith("P1.1") for line in text)


def test_markdown_procedure_and_report():
    record = make_run()
    md = render(record, "procedure", "md")
    assert md.index("**Pre-conditions**") < md.index("**Setup**") < md.index("**Procedure**") < md.index("**Teardown**")
    assert md.index("**Pre-conditions**") < md.index("**P1. System Mode A**") < md.index("P1.1. `sys.mode = 3`")
    assert md.index("P1.2.") < md.index("**Setup**")
    assert "> Shared by every mode A test" in md
    assert "already established" not in md
    assert "**P1. System Mode <B>**" in md or "**P1. System Mode &lt;B&gt;**" in md

    report_md = render(record, "report", "md")
    second = report_md[report_md.index("## test_mode[2]") :]
    second = second[: second.index("## test_broken")]
    assert second.index("**Pre-conditions**") < second.index("**Setup**") < second.index("**Procedure**")
    # The test id is escaped, so brackets and underscores in it stay literal.
    assert "- **P1. System Mode A** ✓ _already established, applied in t.py::test\\_mode\\[1\\]_" in second.splitlines()
    assert "P1.1" not in second
    first = report_md[report_md.index("## test_mode[1]") : report_md.index("## test_mode[2]")]
    assert "- **P1. System Mode A** ✓" in first.splitlines()
    assert first.index("**Pre-conditions**") < first.index("P1.1. `sys.mode = 3`") < first.index("**Setup**")

    alone = dict(record, tests=[record["tests"][1]])
    escaped = ESTABLISHED.replace("_", "\\_").replace("[", "\\[").replace("]", "\\]")
    assert f"- **P1. System Mode A** _{escaped}_" in render(alone, "procedure", "md").splitlines()


def test_html_procedure_and_report():
    record = make_run()
    page = render(record, "procedure", "html")
    assert page.startswith("<!doctype html>")
    pre = page.index('<div class="line section">Pre-conditions</div>')
    assert pre < page.index('<div class="line section">Setup</div>')
    assert pre < page.index('<span class="num">P1</span><span class="body">System Mode A</span>')
    assert page.index('<span class="num">P1.1</span>') < page.index('<div class="line section">Setup</div>')
    assert "already established" not in page
    assert "System Mode &lt;B&gt;" in page and "System Mode <B>" not in page

    report_html = render(record, "report", "html")
    second = report_html[report_html.index(">test_mode[2] <span") :]
    second = second[: second.index(">test_broken <span")]
    assert second.index(">Pre-conditions<") < second.index(">Setup<") < second.index(">Procedure<")
    assert f'System Mode A <span class="note">{ESTABLISHED}</span>' in second
    assert '<span class="num">P1.1</span>' not in second
    assert "System Mode &lt;B&gt;" in report_html and "System Mode <B>" not in report_html

    alone = dict(record, tests=[record["tests"][1]])
    assert f"System Mode A <span class=\"note\">{ESTABLISHED}</span>" in render(alone, "procedure", "html")


def test_reference_of_a_failed_precondition():
    with Recorder():
        with pytest.raises(AssertionError):
            with precondition("System Mode A") as mode:
                assert 1 == 2, "mode not reached"
    with Recorder() as later:
        later.steps.append(reference(mode, "t.py::test_first"))
    assert mode.status == "failed" and later.status == "failed"
    assert procedure(later) == [
        "Pre-conditions",
        "P1. System Mode A   not established, failed in t.py::test_first",
    ]
    assert report(later).splitlines() == [
        "Pre-conditions",
        "P1. System Mode A   ✗ not established, failed in t.py::test_first",
    ]
    # A record whose reference carries the error: the report says why, the procedure does not.
    case = case_record(later)
    case["steps"][0]["error"] = "AssertionError: mode not reached\nassert 1 == 2"
    assert "\n".join(procedure_lines(case)) == "\n".join(procedure(later))
    assert report_lines(case)[1] == (
        "P1. System Mode A   ✗ not established, failed in t.py::test_first"
        " · AssertionError: mode not reached"
    )


def test_procedure_takes_the_case_with_the_fullest_preconditions():
    record = make_run()
    by_id = {p.procedure_id: p for p in procedures(record)}
    proc = by_id["t.py::test_mode"]
    assert proc.precondition is record["tests"][0] and proc.framed
    assert len(proc.variants) == 1  # found established or applied: the same procedure
    assert by_id["t.py::test_broken"].precondition is record["tests"][2]
    # Whichever order the cases come in.
    record["tests"][:2] = reversed(record["tests"][:2])
    proc = next(p for p in procedures(record) if p.procedure_id == "t.py::test_mode")
    assert proc.precondition["id"] == "t.py::test_mode[1]"

    with Recorder() as rec:
        with step("Plain"):
            pass
    (plain,) = procedures(run_record([case_record(rec, test_id="t.py::test_plain")]))
    assert plain.precondition is None and not plain.framed


# The pytest plugin (PRE-3) -------------------------------------------------- #

CONFTEST = '''
import json

import pytest

import stepdoc
from stepdoc import step

events = []


@pytest.hookimpl(optionalhook=True)
def pytest_stepdoc_step_started(item, step):
    events.append(["started", item.name, step.number, step.title, step.applied_in])


@pytest.hookimpl(optionalhook=True)
def pytest_stepdoc_step_finished(item, step):
    events.append(["finished", item.name, step.number, step.status, step.applied_in, len(step.entries)])


@pytest.hookimpl(optionalhook=True)
def pytest_stepdoc_record_ready(config, record):
    with open("events.json", "w") as f:
        json.dump(events, f)


def write(target, value=1):
    stepdoc.record_action(target, "write", value, value_from=("value",))


@pytest.fixture(scope="session")
@stepdoc.precondition("System Mode A", note="Shared by the mode A tests")
def mode_a():
    write("sys.mode", 1)
    with step("Wait for lock"):
        write("pll.enable", 1)
    yield "A"
    with step("Leave mode A"):
        write("sys.mode", 0)


@pytest.fixture
def channel():
    write("ch.reset", 1)  # around the pre-condition: ordinary setup
    with stepdoc.precondition("Channel enabled"):
        write("ch.enable", 1)
    with step("Trim"):
        write("ch.trim", 3)
    yield
    with step("Disable channel"):
        write("ch.enable", 0)


@pytest.fixture(scope="session")
def bench(mode_a):
    return mode_a
'''

SESSION_TESTS = '''
from conftest import write
from stepdoc import step


def test_first(mode_a, channel):
    assert mode_a == "A"
    with step("Measure"):
        write("adc.start")


def test_second(channel, mode_a):
    with step("Measure"):
        write("adc.start")


def test_unrelated():
    with step("Alone"):
        write("x")


def test_indirect(bench):
    with step("Via bench"):
        write("x")
'''


def run(pytester, *args):
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)


def load(pytester, name="run.json"):
    record = json.loads((pytester.path / name).read_text())
    jsonschema.validate(record, SCHEMA)
    return record


def by_name(record):
    return {t["id"].split("::", 1)[1]: t for t in record["tests"]}


def steps(test, section=None):
    return [
        (s["section"], s["number"], s["title"], s.get("applied_in"))
        for s in test["steps"]
        if section is None or s["section"] == section
    ]


def pre(test):
    """(number, title, applied_in) of a test's pre-conditions."""
    return [row[1:] for row in steps(test, "precondition")]


@pytest.fixture
def session_project(pytester):
    pytester.makeconftest(CONFTEST)
    pytester.makepyfile(test_pre=SESSION_TESTS)
    return pytester


def test_session_fixture_is_recorded_once_and_referenced_after(session_project):
    result = run(session_project, "--stepdoc-record=run.json")
    result.assert_outcomes(passed=4)
    tests = by_name(load(session_project))
    first, second, unrelated, indirect = (
        tests[n] for n in ("test_first", "test_second", "test_unrelated", "test_indirect")
    )

    # In full in the test it was applied for; around it, the fixture's ordinary setup.
    assert steps(first) == [
        ("precondition", "P1", "System Mode A", None),
        ("precondition", "P2", "Channel enabled", None),
        ("setup", "S1", "Trim", None),
        ("procedure", "1", "Measure", None),
        ("teardown", "T1", "Disable channel", None),
    ]
    applied = first["steps"][0]
    assert [(e["type"], e["number"]) for e in applied["entries"]] == [("action", "P1.1"), ("step", "P1.2")]
    assert applied["entries"][1]["section"] == "precondition"
    assert applied["note"] == "Shared by the mode A tests"
    assert applied["location"]["file"] == "conftest.py"
    assert [(u["section"], u["target"]["concrete"]) for u in first["unscoped"]] == [("setup", "ch.reset")]

    # Later tests that use the fixture: a reference, before their own pre-conditions.
    assert steps(second) == [
        ("precondition", "P1", "System Mode A", "test_pre.py::test_first"),
        ("precondition", "P2", "Channel enabled", None),
        ("setup", "S1", "Trim", None),
        ("procedure", "1", "Measure", None),
        ("teardown", "T1", "Disable channel", None),
    ]
    ref = second["steps"][0]
    assert ref["entries"] == [] and ref["status"] == applied["status"] == "passed"
    assert ref["note"] == applied["note"] and ref["location"] == applied["location"]
    assert ref["error"] is None
    own = second["steps"][1]
    assert [e["number"] for e in own["entries"]] == ["P2.1"]  # renumbered with its step
    assert second["status"] == "passed"

    # Not in a test that does not use the fixture.
    assert steps(unrelated) == [("procedure", "1", "Alone", None)]

    # Requested only through another fixture; the session teardown is ordinary teardown.
    assert steps(indirect) == [
        ("precondition", "P1", "System Mode A", "test_pre.py::test_first"),
        ("procedure", "1", "Via bench", None),
        ("teardown", "T1", "Leave mode A", None),
    ]
    assert indirect["steps"][0]["entries"] == []


def test_documents_say_already_established(session_project):
    result = run(session_project, "--stepdoc-report=report.txt", "--stepdoc-procedure=procedure.txt")
    result.assert_outcomes(passed=4)
    procedure_text = (session_project.path / "procedure.txt").read_text().split("\n\n")
    first, second, unrelated, indirect = (block.splitlines() for block in procedure_text[:4])
    assert first == [
        "test_pre.py::test_first",
        "Pre-conditions",
        "P1. System Mode A",
        "   Note: Shared by the mode A tests",
        "   P1.1. sys.mode = 1",
        "   P1.2. Wait for lock",
        "      P1.2.1. pll.enable = 1",
        "P2. Channel enabled",
        "   P2.1. ch.enable = 1",
        "Setup",
        "   - ch.reset = 1",
        "S1. Trim",
        "   S1.1. ch.trim = 3",
        "Procedure",
        "1. Measure",
        "   1.1. adc.start = 1",
        "Teardown",
        "T1. Disable channel",
        "   T1.1. ch.enable = 0",
    ]
    assert second[:6] == [
        "test_pre.py::test_second",
        "Pre-conditions",
        "P1. System Mode A   already established, applied in test_pre.py::test_first",
        "   Note: Shared by the mode A tests",
        "P2. Channel enabled",
        "   P2.1. ch.enable = 1",
    ]
    assert unrelated == ["test_pre.py::test_unrelated", "1. Alone", "   1.1. x = 1"]
    assert indirect == [
        "test_pre.py::test_indirect",
        "Pre-conditions",
        "P1. System Mode A   already established, applied in test_pre.py::test_first",
        "   Note: Shared by the mode A tests",
        "Procedure",
        "1. Via bench",
        "   1.1. x = 1",
        "Teardown",
        "T1. Leave mode A",
        "   T1.1. sys.mode = 0",
    ]
    report_text = (session_project.path / "report.txt").read_text()
    assert "P1. System Mode A   ✓ already established, applied in test_pre.py::test_first" in report_text.splitlines()
    assert report_text.count("P1.1. sys.mode = 1") == 1


@pytest.mark.parametrize("ext", ["md", "html"])
def test_markdown_and_html_documents_from_a_run(session_project, ext):
    result = run(session_project, f"--stepdoc-report=report.{ext}", f"--stepdoc-procedure=procedure.{ext}")
    result.assert_outcomes(passed=4)
    for name in ("procedure", "report"):
        page = (session_project.path / f"{name}.{ext}").read_text()
        test_id = "test_pre.py::test_first" if ext == "html" else "test\\_pre.py::test\\_first"
        assert page.count(f"already established, applied in {test_id}") == 2
        assert page.index("Pre-conditions") < page.index("System Mode A") < page.index("Setup")
        assert page.count("Pre-conditions") == 3  # not for test_unrelated


def test_hooks_see_the_references(session_project):
    run(session_project, "--stepdoc-record=run.json").assert_outcomes(passed=4)
    events = json.loads((session_project.path / "events.json").read_text())

    def of(test):
        return [e[:1] + e[2:] for e in events if e[1] == test]

    assert of("test_first")[:4] == [
        ["started", "P1", "System Mode A", None],
        ["started", "P1.2", "Wait for lock", None],
        ["finished", "P1.2", "passed", None, 1],
        ["finished", "P1", "passed", None, 2],
    ]
    assert not [e for e in of("test_first") if e[3] is not None]
    ref = "test_pre.py::test_first"
    assert ["started", "P1", "System Mode A", ref] in of("test_second")
    assert ["finished", "P1", "passed", ref, 0] in of("test_second")
    assert of("test_indirect") == [
        ["started", "P1", "System Mode A", ref],
        ["finished", "P1", "passed", ref, 0],
        ["started", "1", "Via bench", None],
        ["finished", "1", "passed", None, 1],
        ["started", "T1", "Leave mode A", None],
        ["finished", "T1", "passed", None, 1],
    ]
    # Told once per test, after its setup and before its procedure.
    second = of("test_second")
    assert [e[0] for e in second if e[3] == ref] == ["started", "finished"]
    assert second.index(["finished", "P1", "passed", ref, 0]) < second.index(["started", "1", "Measure", None])
    assert not [e for e in of("test_unrelated") if e[1].startswith("P")]


def test_running_one_later_test_alone_applies_it_there(session_project):
    run(session_project, "--stepdoc-record=run.json", "-k", "second or indirect").assert_outcomes(passed=2)
    tests = by_name(load(session_project))
    assert pre(tests["test_second"]) == [("P1", "System Mode A", None), ("P2", "Channel enabled", None)]
    assert pre(tests["test_indirect"]) == [("P1", "System Mode A", "test_pre.py::test_second")]


def test_inert_without_options(session_project):
    result = run(session_project)
    result.assert_outcomes(passed=4)
    assert not list(session_project.path.glob("*.json"))


def test_dry_run_keeps_the_references(session_project):
    run(session_project, "--stepdoc-dry-run", "--stepdoc-record=run.json").assert_outcomes(passed=4)
    record = load(session_project)
    assert record["dry_run"] is True
    tests = by_name(record)
    assert pre(tests["test_first"]) == [("P1", "System Mode A", None), ("P2", "Channel enabled", None)]
    assert pre(tests["test_second"]) == [
        ("P1", "System Mode A", "test_pre.py::test_first"),
        ("P2", "Channel enabled", None),
    ]
    assert {s["status"] for t in record["tests"] for s in t["steps"]} == {"unknown"}


PARAM_CONFTEST = '''
import pytest

import stepdoc


@pytest.fixture(scope="session", params=["A", "B", "C"])
def mode(request):
    with stepdoc.precondition(f"System Mode {request.param}"):
        stepdoc.record_action("sys.mode", "write", request.param)
    return request.param
'''

PARAM_TESTS = '''
from stepdoc import step


def test_gain(mode):
    step("Measure gain")


def test_noise(mode):
    step("Measure noise")


def test_offset(mode):
    step("Measure offset")
'''


def test_parametrized_session_fixture_is_applied_again_for_each_parameter(pytester):
    pytester.makeconftest(PARAM_CONFTEST)
    pytester.makepyfile(test_modes=PARAM_TESTS)
    result = run(pytester, "--stepdoc-record=run.json", "--stepdoc-procedure=procedure.txt")
    result.assert_outcomes(passed=9)
    record = load(pytester)
    tests = by_name(record)
    assert len(tests) == 9
    for letter in "ABC":
        title = f"System Mode {letter}"
        users = [t for t in record["tests"] if t["id"].endswith(f"[{letter}]")]
        assert len(users) == 3
        # pytest groups the tests by parameter: the first of each group applies the mode.
        appliers = [t for t in users if pre(t) == [("P1", title, None)]]
        assert len(appliers) == 1 and appliers[0] is users[0]
        (full,) = [s for s in appliers[0]["steps"] if s["section"] == "precondition"]
        assert full["entries"][0]["value"]["concrete"] == letter
        for later in users[1:]:
            assert pre(later) == [("P1", title, appliers[0]["id"])]
            assert later["steps"][0]["entries"] == []
    assert tests["test_noise[B]"]["steps"][0]["applied_in"] == "test_modes.py::test_gain[B]"
    # One procedure per test function: its cases differ in the mode only where it was applied.
    text = (pytester.path / "procedure.txt").read_text()
    assert "test_modes.py::test_gain" in text and "Pre-conditions" in text


SCOPED_CONFTEST = '''
import pytest

import stepdoc


def write(target, value=1):
    stepdoc.record_action(target, "write", value, value_from=("value",))


@pytest.fixture(scope="module")
def module_mode(request):
    with stepdoc.precondition("Module mode"):
        write("mod.mode", request.module.__name__)


@pytest.fixture(scope="class")
@stepdoc.precondition("Class mode")
def class_mode(request):
    write("cls.mode", request.cls.__name__)
    yield
    with stepdoc.step("Leave class mode"):
        write("cls.mode", 0)


@pytest.fixture
def own():
    with stepdoc.precondition("Own state"):
        write("own")
'''

SCOPED_TESTS = '''
import pytest


def test_one(module_mode):
    pass


def test_two(own, module_mode):
    pass


class TestFirst:
    def test_a(self, class_mode):
        pass

    def test_b(self, class_mode, module_mode, own):
        pass

    def test_c(self):
        pass


class TestSecond:
    def test_a(self, own, class_mode):
        pass

    def test_b(self, class_mode):
        pass
'''


def test_module_and_class_scoped_fixtures(pytester):
    pytester.makeconftest(SCOPED_CONFTEST)
    pytester.makepyfile(test_m1=SCOPED_TESTS, test_m2=SCOPED_TESTS)
    run(pytester, "--stepdoc-record=run.json").assert_outcomes(passed=14)
    record = load(pytester)
    assert [t["id"].split(".py")[0] for t in record["tests"]] == ["test_m1"] * 7 + ["test_m2"] * 7
    for module in ("test_m1", "test_m2"):
        tests = {t["id"].split("::", 1)[1]: t for t in record["tests"] if t["id"].startswith(module)}
        one = f"{module}.py::test_one"
        assert pre(tests["test_one"]) == [("P1", "Module mode", None)]
        assert tests["test_one"]["steps"][0]["entries"][0]["value"]["concrete"] == module
        # The reference first, though the test's own fixture ran before the lookup.
        assert pre(tests["test_two"]) == [("P1", "Module mode", one), ("P2", "Own state", None)]

        first_a = f"{module}.py::TestFirst::test_a"
        assert pre(tests["TestFirst::test_a"]) == [("P1", "Class mode", None)]
        # Two references, in the order the pre-conditions were applied.
        assert pre(tests["TestFirst::test_b"]) == [
            ("P1", "Module mode", one),
            ("P2", "Class mode", first_a),
            ("P3", "Own state", None),
        ]
        assert [e["number"] for e in tests["TestFirst::test_b"]["steps"][2]["entries"]] == ["P3.1"]
        assert pre(tests["TestFirst::test_c"]) == []
        assert steps(tests["TestFirst::test_c"], "teardown") == [("teardown", "T1", "Leave class mode", None)]

        # A new class applies the class fixture again.
        second_a = f"{module}.py::TestSecond::test_a"
        # pytest sets up the wider scope first, whatever the order of the arguments.
        assert pre(tests["TestSecond::test_a"]) == [("P1", "Class mode", None), ("P2", "Own state", None)]
        assert pre(tests["TestSecond::test_b"]) == [("P1", "Class mode", second_a)]
        assert tests["TestSecond::test_b"]["steps"][0]["entries"] == []


DYNAMIC_TESTS = '''
import pytest

import stepdoc


@pytest.fixture(scope="session")
@stepdoc.precondition("System Mode A")
def mode_a():
    stepdoc.record_action("sys.mode", "write", 1)


@pytest.fixture(scope="session")
def bench(mode_a):
    """Applies nothing itself."""


@pytest.fixture(scope="session")
def rack(bench):
    """Two fixtures away from the pre-condition."""


@pytest.fixture
def late(request):
    return request.getfixturevalue("mode_a")


@pytest.fixture(scope="session")
def plain():
    with stepdoc.step("No pre-condition here"):
        pass


def test_applies(rack):
    pass


def test_through_two_fixtures(rack):
    pass


def test_looked_up_in_setup(late):
    pass


def test_other_fixture(plain):
    pass


def test_other_fixture_again(plain):
    pass


@pytest.mark.usefixtures("mode_a")
def test_usefixtures():
    pass
'''


def test_fixture_requested_through_other_fixtures(pytester):
    pytester.makepyfile(test_dyn=DYNAMIC_TESTS)
    run(pytester, "--stepdoc-record=run.json").assert_outcomes(passed=6)
    tests = by_name(load(pytester))
    applied = "test_dyn.py::test_applies"
    assert pre(tests["test_applies"]) == [("P1", "System Mode A", None)]
    assert pre(tests["test_through_two_fixtures"]) == [("P1", "System Mode A", applied)]
    assert pre(tests["test_looked_up_in_setup"]) == [("P1", "System Mode A", applied)]
    assert pre(tests["test_usefixtures"]) == [("P1", "System Mode A", applied)]
    # A shared fixture without pre-conditions leaves no reference.
    assert steps(tests["test_other_fixture"]) == [("setup", "S1", "No pre-condition here", None)]
    assert steps(tests["test_other_fixture_again"]) == []


FAILING_TESTS = '''
import pytest

import stepdoc


@pytest.fixture(scope="session")
@stepdoc.precondition("System Mode A")
def mode_a():
    stepdoc.record_action("sys.mode", "write", 1)
    assert 1 == 2, "mode A not reached"


@pytest.fixture(scope="session")
def mode_b():
    with stepdoc.precondition("System Mode B"):
        raise RuntimeError("no answer from the device")


@pytest.fixture(scope="session")
def soft():
    with stepdoc.precondition("Lock"):
        stepdoc.attach_check(False, "PLL locked")


def test_a1(mode_a):
    pass


def test_a2(mode_a):
    pass


def test_b1(mode_b):
    pass


def test_b2(mode_b):
    pass


def test_soft1(soft):
    pass


def test_soft2(soft):
    pass
'''


def test_precondition_that_fails_in_a_shared_fixture(pytester):
    pytester.makepyfile(test_bad=FAILING_TESTS)
    result = run(pytester, "--stepdoc-record=run.json", "--stepdoc-report=report.txt")
    # pytest raises a failed session fixture's error again for each test that uses it.
    result.assert_outcomes(errors=4, passed=2)
    tests = by_name(load(pytester))

    a1, a2 = tests["test_a1"]["steps"], tests["test_a2"]["steps"]
    assert pre(tests["test_a1"]) == [("P1", "System Mode A", None)]
    assert a1[0]["status"] == "failed" and a1[0]["error"].startswith("AssertionError")
    assert [(e["type"], e["number"]) for e in a1[0]["entries"]] == [("action", "P1.1"), ("check", "P1.2")]
    assert a1[0]["entries"][1]["passed"] is False
    # The later test did not get the state either, and says so with the same status.
    assert pre(tests["test_a2"]) == [("P1", "System Mode A", "test_bad.py::test_a1")]
    assert (a2[0]["status"], a2[0]["entries"]) == ("failed", [])
    assert tests["test_a2"]["status"] == "failed"

    b1, b2 = tests["test_b1"]["steps"], tests["test_b2"]["steps"]
    assert b1[0]["status"] == "error" and b1[0]["error"] == "RuntimeError: no answer from the device"
    assert pre(tests["test_b2"]) == [("P1", "System Mode B", "test_bad.py::test_b1")]
    assert (b2[0]["status"], b2[0]["entries"]) == ("error", [])
    assert tests["test_b2"]["status"] == "error"

    # A failed check (the fixture itself returned): the tests ran, the status is carried over.
    s1, s2 = tests["test_soft1"]["steps"], tests["test_soft2"]["steps"]
    assert s1[0]["status"] == "failed" and s1[0]["error"] is None
    assert pre(tests["test_soft2"]) == [("P1", "Lock", "test_bad.py::test_soft1")]
    assert s2[0]["status"] == "failed" and tests["test_soft2"]["status"] == "failed"

    report_lines = (pytester.path / "report.txt").read_text().splitlines()
    (line,) = [text for text in report_lines if text.startswith("P1. System Mode B") and "established" in text]
    assert line == (
        "P1. System Mode B   ✗ not established, failed in test_bad.py::test_b1"
        " · RuntimeError: no answer from the device"
    )


OTHER_TESTS = '''
import pytest

import stepdoc


@pytest.fixture(scope="session")
@stepdoc.precondition("System Mode A")
def mode_a():
    stepdoc.step("Select mode A")


@pytest.fixture
def broken():
    raise RuntimeError("boom")


def test_applies(mode_a):
    pass


def test_setup_fails(mode_a, broken):
    pass


class TestOverride:
    @pytest.fixture(scope="class")
    def mode_a(self):
        with stepdoc.precondition("Local mode"):
            pass

    def test_local(self, mode_a):
        pass

    def test_local_again(self, mode_a):
        pass


def test_after_the_class(mode_a):
    pass
'''


def test_overridden_fixture_and_a_test_whose_setup_fails(pytester):
    pytester.makepyfile(test_other=OTHER_TESTS)
    run(pytester, "--stepdoc-record=run.json").assert_outcomes(passed=4, errors=1)
    tests = by_name(load(pytester))
    applied = "test_other.py::test_applies"
    # The state was there when another fixture of the test failed.
    assert pre(tests["test_setup_fails"]) == [("P1", "System Mode A", applied)]
    assert tests["test_setup_fails"]["outcome"] == "error"
    # The class overrides the fixture: its tests have that one's pre-condition only.
    assert pre(tests["TestOverride::test_local"]) == [("P1", "Local mode", None)]
    assert pre(tests["TestOverride::test_local_again"]) == [
        ("P1", "Local mode", "test_other.py::TestOverride::test_local")
    ]
    assert pre(tests["test_after_the_class"]) == [("P1", "System Mode A", applied)]


SKIPPED_PARAM_TESTS = '''
import pytest

import stepdoc


@pytest.fixture(scope="session", params=["A", pytest.param("B", marks=pytest.mark.skip(reason="no B bench"))])
def mode(request):
    with stepdoc.precondition(f"System Mode {request.param}"):
        pass


def test_gain(mode):
    pass
'''


def test_skipped_test_does_not_list_another_parameters_precondition(pytester):
    pytester.makepyfile(test_skipped=SKIPPED_PARAM_TESTS)
    run(pytester, "--stepdoc-record=run.json").assert_outcomes(passed=1, skipped=1)
    tests = by_name(load(pytester))
    assert pre(tests["test_gain[A]"]) == [("P1", "System Mode A", None)]
    assert pre(tests["test_gain[B]"]) == []  # mode B was never applied, and mode A is not its state


WRITE = '''

def write(target, value=1):
    stepdoc.record_action(target, "write", value, value_from=("value",))
'''

TEARDOWN_TESTS = '''
import pytest

import stepdoc
from stepdoc import step
''' + WRITE + '''

@pytest.fixture
@stepdoc.precondition("Channel enabled")
def channel():
    write("ch.enable", 1)
    yield "ch1"
    write("ch.flush", 1)
    with step("Disable channel"):
        write("ch.enable", 0)
    with stepdoc.precondition("Idle again"):
        write("ch.idle", 1)


def test_one(channel):
    assert channel == "ch1"
    with step("Measure"):
        with stepdoc.precondition("Range selected"):
            write("adc.range", 2)
        write("adc.start", 1)


def test_two(channel):
    pass
'''


def test_steps_after_the_yield_go_to_teardown(pytester):
    pytester.makepyfile(test_td=TEARDOWN_TESTS)
    run(pytester, "--stepdoc-record=run.json", "--stepdoc-procedure=procedure.txt").assert_outcomes(passed=2)
    tests = by_name(load(pytester))
    one = tests["test_one"]
    # A block written in the test body or in teardown is a pre-condition all the same (PRE-1).
    assert sorted(steps(one)) == sorted([
        ("precondition", "P1", "Channel enabled", None),
        ("precondition", "P2", "Range selected", None),
        ("procedure", "1", "Measure", None),
        ("teardown", "T1", "Disable channel", None),
        ("precondition", "P3", "Idle again", None),
    ])
    by_number = {s["number"]: s for s in one["steps"]}
    assert [e["number"] for e in by_number["P1"]["entries"]] == ["P1.1"]
    assert [e["target"]["concrete"] for e in by_number["1"]["entries"]] == ["adc.start"]
    assert [(u["section"], u["target"]["concrete"]) for u in one["unscoped"]] == [("teardown", "ch.flush")]
    # A function-scoped fixture runs again for each test: no reference.
    assert pre(tests["test_two"]) == [("P1", "Channel enabled", None), ("P2", "Idle again", None)]
    text = (pytester.path / "procedure.txt").read_text().splitlines()
    assert text.index("Pre-conditions") < text.index("Procedure") < text.index("Teardown")
    assert text[text.index("Teardown") + 1 : text.index("Teardown") + 4] == [
        "   - ch.flush = 1",
        "T1. Disable channel",
        "   T1.1. ch.enable = 0",
    ]


PARAM_CASES = '''
import pytest

import stepdoc
from stepdoc import step
''' + WRITE + '''

@pytest.fixture(scope="module")
@stepdoc.precondition("System Mode A")
def mode_a():
    write("sys.mode", 1)


@pytest.mark.parametrize("level", [1, 2, 3])
def test_levels(mode_a, level):
    with step("Set level"):
        write("dac.level", level)
'''


@pytest.mark.parametrize("ext", ["txt", "md", "html"])
def test_procedure_of_parametrized_cases_shows_the_precondition_in_full(pytester, ext):
    pytester.makepyfile(test_cases=PARAM_CASES)
    run(pytester, "--stepdoc-record=run.json", f"--stepdoc-procedure=procedure.{ext}").assert_outcomes(passed=3)
    record = load(pytester)
    assert [pre(t) for t in record["tests"]] == [
        [("P1", "System Mode A", None)],
        [("P1", "System Mode A", "test_cases.py::test_levels[1]")],
        [("P1", "System Mode A", "test_cases.py::test_levels[1]")],
    ]
    page = (pytester.path / f"procedure.{ext}").read_text()
    assert page.count("Pre-conditions") == 1 and page.count("System Mode A") == 1
    assert "sys.mode = 1" in page and "dac.level = <level>" in page.replace("&lt;", "<").replace("&gt;", ">")
    assert "already established" not in page
    assert "Variant for" not in page and "Procedure for" not in page


RETRY_CONFTEST = '''
from _pytest.runner import runtestprotocol


def pytest_runtest_protocol(item, nextitem):
    """Runs every test twice inside one protocol, as pytest-reporter's retries do."""
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    runtestprotocol(item, nextitem=nextitem, log=False)
    for report in runtestprotocol(item, nextitem=nextitem, log=False):
        item.ihook.pytest_runtest_logreport(report=report)
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True
'''


def test_rerun_inside_one_recorder_tells_the_hooks_again(session_project):
    session_project.makeconftest(CONFTEST + RETRY_CONFTEST)
    # One more test after them: pytest tears the session fixture down after the
    # last test's first attempt, so that one would apply it again.
    session_project.makepyfile(test_zz_last="def test_last(mode_a):\n    pass\n")
    result = run(session_project, "--stepdoc-record=run.json", "-k", "first or indirect or last")
    result.assert_outcomes(passed=3)
    tests = by_name(load(session_project))
    ref = "test_pre.py::test_first"
    # The record lists the established pre-condition once, whatever the number of attempts.
    assert pre(tests["test_indirect"]) == [("P1", "System Mode A", ref)]
    assert [row for row in pre(tests["test_first"]) if row[1] == "System Mode A"] == [("P1", "System Mode A", None)]
    events = json.loads((session_project.path / "events.json").read_text())
    indirect = [e[:1] + e[2:] for e in events if e[1] == "test_indirect"]
    assert indirect == [
        ["started", "P1", "System Mode A", ref],
        ["finished", "P1", "passed", ref, 0],
        ["started", "1", "Via bench", None],
        ["finished", "1", "passed", None, 1],
        ["started", "P1", "System Mode A", ref],
        ["finished", "P1", "passed", ref, 0],
        ["started", "2", "Via bench", None],
        ["finished", "2", "passed", None, 1],
    ]
    # The second attempt of the test that applied it finds it established as well.
    first = [e[:1] + e[2:] for e in events if e[1] == "test_first"]
    assert first.count(["started", "P1", "System Mode A", None]) == 1
    assert first.count(["started", "P1", "System Mode A", ref]) == 1


@pytest.mark.skipif(importlib.util.find_spec("xdist") is None, reason="pytest-xdist is not installed")
def test_xdist_references_point_at_a_test_of_the_same_worker(session_project):
    session_project.makepyfile(
        test_more="\n\n".join(f"def test_m{i}(mode_a):\n    pass" for i in range(8)) + "\n"
    )
    result = run(session_project, "-n", "2", "--stepdoc-record=run.json", "--stepdoc-report=report.txt")
    result.assert_outcomes(passed=12)
    record = load(session_project)
    assert len(record["tests"]) == 12
    by_id = {t["id"]: t for t in record["tests"]}
    users = [t for t in record["tests"] if "unrelated" not in t["id"]]
    appliers = []
    for test in users:
        (mode,) = [s for s in test["steps"] if s["title"] == "System Mode A"]
        assert (mode["section"], mode["number"], mode["status"]) == ("precondition", "P1", "passed")
        if "applied_in" not in mode:
            assert len(mode["entries"]) == 2
            appliers.append(test["id"])
        else:
            assert mode["entries"] == []
            # It names a test of this run that applied the pre-condition itself.
            origin = by_id[mode["applied_in"]]
            assert "applied_in" not in origin["steps"][0] and origin["steps"][0]["title"] == "System Mode A"
    assert 1 <= len(appliers) <= 2  # once per worker that ran a test using the fixture
    assert pre(by_id["test_pre.py::test_unrelated"]) == []
    report_text = (session_project.path / "report.txt").read_text()
    assert report_text.count("already established, applied in") == len(users) - len(appliers)


# The verify adapter --------------------------------------------------------- #
# pytest-verifier is not installed here; tests/test_adapters.py's fake stands in.

VERIFY_PRE_TESTS = '''
import stepdoc
from stepdoc import step


@stepdoc.precondition("Clock locked")
def lock(verify):
    verify.equal(3, 3, name="PLL lock")


def test_user(verify):
    with stepdoc.precondition("Supply on"):
        verify.equal(5, 5, name="Supply voltage")
        with stepdoc.precondition("Reference on"):
            verify.equal(6, 7, name="Reference")
        verify.equal(8, 8, name="Supply current")
    lock(verify)
    with step("Measure"):
        verify.equal(4, 4, name="Reading")
'''

VERIFY_IN_A_STEP = '''
import stepdoc
from stepdoc import step


def test_user(verify):
    with step("Measure"):
        with stepdoc.precondition("Range selected"):
            verify.equal(7, 8, name="Range")
        verify.equal(4, 4, name="Reading")
'''

VERIFY_DECORATED_IN_A_STEP = '''
import stepdoc
from stepdoc import step


@stepdoc.precondition("Range selected")
def select_range(verify):
    verify.equal(7, 8, name="Range")


def test_user(verify):
    with step("Measure"):
        select_range(verify)
        verify.equal(4, 4, name="Reading")
'''


def run_with_verify(pytester, source):
    pytester.makepyfile(
        load_fake_verifier=FAKE_LOADER, fake_verifier=FAKE_VERIFY, verify_specs=VERIFY_SPECS,
        test_users=source,
    )
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "load_fake_verifier",
        "--stepdoc-record=run.json", "--stepdoc-report=report.txt",
    )
    result.assert_outcomes(failed=1)
    (test,) = load(pytester)["tests"]
    return test, {s["title"]: s for s in test["steps"]}


def checks(s):
    # The text is "'Name' == 5" or "'Name': expr == 5".
    return [(e["number"], e["text"].split("'")[1], e["passed"]) for e in s["entries"] if e["type"] == "check"]


def test_verify_checks_are_placed_into_their_precondition(pytester):
    test, by_title = run_with_verify(pytester, VERIFY_PRE_TESTS)
    assert test["unscoped"] == []
    assert {t: (s["section"], s["number"]) for t, s in by_title.items()} == {
        "Supply on": ("precondition", "P1"),
        "Clock locked": ("precondition", "P2"),
        "Measure": ("procedure", "1"),
    }
    supply = by_title["Supply on"]
    assert [(e["type"], e["number"]) for e in supply["entries"]] == [
        ("check", "P1.1"),
        ("step", "P1.2"),
        ("check", "P1.3"),
    ]
    assert checks(supply) == [("P1.1", "Supply voltage", True), ("P1.3", "Supply current", True)]
    inner = supply["entries"][1]
    assert (inner["title"], inner["section"]) == ("Reference on", "precondition")
    assert checks(inner) == [("P1.2.1", "Reference", False)]
    # The judged check decides the status of the pre-condition and of the one around it.
    assert (inner["status"], supply["status"]) == ("failed", "failed")
    # A decorated helper: located at its first decorator line, the check is still its own.
    assert checks(by_title["Clock locked"]) == [("P2.1", "PLL lock", True)]
    assert by_title["Clock locked"]["status"] == "passed"
    assert checks(by_title["Measure"]) == [("1.1", "Reading", True)]
    report_text = (pytester.path / "report.txt").read_text()
    assert report_text.index("Pre-conditions") < report_text.index("Verify 'Supply voltage'") < report_text.index("Procedure")


@pytest.mark.parametrize("source", [VERIFY_IN_A_STEP, VERIFY_DECORATED_IN_A_STEP], ids=["block", "decorated"])
def test_verify_checks_of_a_precondition_written_inside_a_step(pytester, source):
    _, by_title = run_with_verify(pytester, source)
    assert by_title["Range selected"]["section"] == "precondition"
    assert checks(by_title["Range selected"]) == [("P1.1", "Range", False)]
    assert by_title["Range selected"]["status"] == "failed"
    assert checks(by_title["Measure"]) == [("1.1", "Reading", True)]
    assert by_title["Measure"]["status"] == "passed"


# --------------------------------------------------------------------------- #
# Found in review
# --------------------------------------------------------------------------- #


def test_block_inside_a_quiet_step_still_records_its_actions():
    with Recorder() as rec:
        with step("Reset", record=False):
            write("hidden")
            with precondition("System Mode A"):
                write("sys.mode")
            write("hidden.too")
    reset, mode = rec.steps
    assert (reset.number, mode.number) == ("1", "P1")
    assert [a.target for a in mode.actions] == ["sys.mode"]
    assert reset.entries == []


def test_decorator_above_pytest_fixture_is_a_usage_error():
    with pytest.raises(TypeError, match="put @pytest.fixture above"):

        @precondition("System Mode A")
        @pytest.fixture
        def mode_a():
            yield


REVIEW_TESTS = '''
import pytest

import stepdoc


@pytest.fixture(scope="session")
def base():
    with stepdoc.precondition("Base"):
        stepdoc.record_action("sys.base", "write", 1)
    return "base"


class TestOverride:
    @pytest.fixture
    def base(self, base):
        return base + "!"

    def test_first(self, base):
        assert base == "base!"

    def test_second(self, base):
        assert base == "base!"


def test_in_the_body(request):
    assert request.getfixturevalue("base") == "base"


@pytest.mark.skip(reason="not today")
def test_skipped(base):
    pass
'''


def test_references_through_an_override_and_from_the_test_body(pytester):
    pytester.makepyfile(test_review=REVIEW_TESTS)
    run(pytester, "--stepdoc-record=run.json").assert_outcomes(passed=3, skipped=1)
    tests = by_name(load(pytester))
    applied = "test_review.py::TestOverride::test_first"
    assert pre(tests["TestOverride::test_first"]) == [("P1", "Base", None)]
    # The override asks for the fixture it overrides: its tests use that state too.
    assert pre(tests["TestOverride::test_second"]) == [("P1", "Base", applied)]
    # Asked for in the test body, after the setup.
    assert pre(tests["test_in_the_body"]) == [("P1", "Base", applied)]
    assert pre(tests["test_skipped"]) == []
