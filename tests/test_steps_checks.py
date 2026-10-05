"""STEP-1…6 and CHK-1…3, 7, 8 in the core, without pytest's plugin."""

import pytest

from stepdoc import Check, CheckTypeError, Recorder, attach_check, register_check_converter, step
from stepdoc.core import recorder as recorder_mod


def statuses(steps):
    return {s.number: s.status for s in steps}


def test_status_combines_checks_and_children():
    with Recorder() as rec:
        with step("All good"):
            attach_check(True, "ok")
        with step("Child fails"):
            with step("Inner"):
                attach_check(False, "bad")
            attach_check(True, "fine")
        with step("Only documented"):
            attach_check(Check("not judged", passed=None))
    assert statuses(rec.steps) == {"1": "passed", "2": "failed", "3": "passed"}
    assert rec.steps[1].children[0].status == "failed"
    assert rec.status == "failed"


def test_exception_is_attached_and_reraised():
    with Recorder() as rec:
        with pytest.raises(ZeroDivisionError):
            with step("Divide"):
                1 / 0
        with pytest.raises(AssertionError):
            with step("Assert"):
                assert 1 == 2, "values differ"
    divide, check = rec.steps
    assert divide.status == "error" and divide.error.startswith("ZeroDivisionError")
    assert check.status == "failed"
    assert check.checks[0].text == "1 == 2" and check.checks[0].passed is False
    assert check.checks[0].detail.startswith("values differ")


def test_leaf_step_forms():
    with Recorder() as rec:
        step("Bool", check=True)
        step("Callable", check=lambda: False)
        step("Check object", check=Check("custom", passed=None))
    assert statuses(rec.steps) == {"1": "passed", "2": "failed", "3": "passed"}
    assert rec.steps[0].location.file.endswith("test_steps_checks.py")
    with pytest.raises(TypeError):
        with step("x", check=True):
            pass


def test_unknown_check_type_names_the_tool():
    class Foreign:
        pass

    Foreign.__module__ = "pytest_verify.descriptors"
    with Recorder():
        with pytest.raises(CheckTypeError, match="'pytest_verify'"):
            step("x", check=Foreign())
        with pytest.raises(CheckTypeError, match="returned int"):
            step("x", check=lambda: 1)


def test_registered_converter(monkeypatch):
    monkeypatch.setattr(recorder_mod, "_converters", [])

    class Verdict:
        def __init__(self, ok):
            self.ok = ok

    register_check_converter(lambda o: isinstance(o, Verdict), lambda o: Check("verdict", passed=o.ok))
    with Recorder() as rec:
        step("Converted", check=Verdict(False))
    assert rec.steps[0].status == "failed"
    assert rec.steps[0].checks[0].text == "verdict"


def test_dry_run_records_without_judging():
    with Recorder(dry_run=True) as rec:
        with step("Fails in simulation"):
            attach_check(False, "would fail")
            assert False, "simulated value is off"
        with step("Still documented"):
            pass
    first, second = rec.steps
    assert [c.passed for c in first.checks] == [None, None]
    assert first.status == second.status == "unknown"
    assert rec.status == "unknown"


def test_dry_run_does_not_swallow_errors():
    with Recorder(dry_run=True):
        with pytest.raises(RuntimeError):
            with step("Broken"):
                raise RuntimeError("backend down")


def test_decorator_and_nesting_numbers():
    @step("Helper")
    def helper():
        attach_check(True, "inside")

    with Recorder() as rec:
        with step("Outer"):
            attach_check(True, "first")
            helper()
    outer = rec.steps[0]
    assert [e.number for e in outer.entries] == ["1.1", "1.2"]
    assert outer.children[0].checks[0].number == "1.2.1"


def test_no_recorder_is_a_no_op():
    with step("Nothing"):
        assert attach_check(True) is None
    step("Leaf", check=True)
