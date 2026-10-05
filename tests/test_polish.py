"""v0.2 core polish: polling collapse (ACT-4), reads merged into checks (ACT-5),
quiet steps (STEP-7) and manual names (SYM-4)."""

import pytest

import stepdoc
from stepdoc import Recorder, step
from stepdoc.core.record import case_record
from tests.helpers import procedure, report


def poll(dev):
    while not dev.map.status.pll_locked:
        pass


def test_polling_collapses_into_one_entry(dev, rec):
    with step("Lock"):
        poll(dev)
        a = dev.map.status.pll_locked
        b = dev.map.status.pll_locked  # another line: intentional, kept
    entries = rec.steps[0].entries
    assert [(e.target, e.count) for e in entries] == [
        ("dev.map.status.pll_locked", 3),
        ("dev.map.status.pll_locked", 1),
        ("dev.map.status.pll_locked", 1),
    ]
    assert a is b is True
    record = case_record(rec)
    first = record["steps"][0]["entries"][0]
    assert first["count"] == 3 and first["t_last"] >= first["t"]
    assert first["value"]["concrete"] is True


def test_writes_never_collapse(dev, rec):
    for _ in range(3):
        dev.map.ctrl.enable = 1
    assert [a.count for a in rec.unscoped] == [1, 1, 1]


def test_repeated_requests_collapse_when_identical(api, rec):
    with step("Wait for job"):
        for _ in range(3):
            api.get("/version")
    with step("Different bodies"):
        for i in range(2):
            api.post("/users", json={"name": f"u{i}", "age": i})
    wait, posts = rec.steps
    assert [a.count for a in wait.actions] == [3]
    assert [a.count for a in posts.actions] == [1, 1]
    assert procedure(rec)[:2] == ["1. Wait for job", "   1.1. Poll GET /version"]
    assert "1.1. Poll GET /version -> 404 (3 calls, " in report(rec)


def test_collapse_can_be_turned_off(dev):
    with Recorder(collapse_repeats=False) as rec:
        poll(dev)
    assert len(rec.unscoped) == 3


def test_failed_assert_absorbs_the_read_on_its_line(dev, rec):
    with pytest.raises(AssertionError):
        with step("Check ADC"):
            dev.map.ctrl.mode = 1
            assert dev.map.adc.value == 99
    entries = rec.steps[0].entries
    assert [type(e).__name__ for e in entries] == ["Action", "Check"]
    check = entries[1]
    assert check.number == "1.2" and [r.target for r in check.reads] == ["dev.map.adc.value"]
    assert procedure(rec) == [
        "1. Check ADC",
        "   1.1. dev.map.ctrl.mode = 1",
        "   1.2. Verify dev.map.adc.value == 99",
    ]
    assert "   1.2. Verify dev.map.adc.value == 99 -> got 0   ✗" in report(rec)


@step("Reset device", record=False)
def reset(dev):
    dev.map.ctrl.enable = 0
    dev.map.ctrl.mode = 0
    stepdoc.attach_check(True, "reset done")


def test_quiet_step_keeps_checks_drops_actions(dev, rec):
    reset(dev)
    with step("Configure"):
        with step("Quiet block", record=False):
            dev.map.ctrl.mode = 2
            with step("Nested"):
                dev.map.ctrl.mode = 3
        dev.map.ctrl.enable = 1
    assert procedure(rec) == [
        "1. Reset device",
        "   1.1. Verify reset done",
        "2. Configure",
        "   2.1. Quiet block",
        "      2.1.1. Nested",
        "   2.2. dev.map.ctrl.enable = 1",
    ]


def apply(dev, c):
    dev.map.dac.level = c


def test_manual_names(dev, api, rec):
    level = 100
    dev.map.dac.level = stepdoc.value("code", level * 2)  # inline: from the source
    apply(dev, stepdoc.value("code", level + 1))  # through a helper: by value
    name = "Ana"
    api.post("/users", json={"name": stepdoc.value("user", name), "age": 3})
    rec.resolve()
    assert [a.symbolic_value for a in rec.unscoped] == [
        "<code>",
        "<code>",
        '{"name": <user>, "age": 3}',
    ]
    assert stepdoc.value("x", 5) == 5  # returns the value unchanged
