"""ACT-4/ACT-10: writes from one source line collapse whatever value they send;
the entry keeps every value (``Action.values``) and the report shows them."""

import json
from pathlib import Path

import jsonschema
import stepdoc
from stepdoc import Check, Location, Recorder, Repeat, look_through, repeat, step
from stepdoc.core.record import case_record, run_record
from stepdoc.renderers import render
from tests.helpers import procedure, report

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "src/stepdoc/schema/run-record.schema.json").read_text()
)
X = 16


def _line(rec, number):
    return next(x for x in report(rec).splitlines() if x.lstrip().startswith(f"{number}. "))


def test_for_loop_of_varying_writes_is_one_line(dev, rec):
    with step("Fill"):
        for i in range(X):
            dev.map.pulse.width = i
    (a,) = rec.steps[0].entries
    assert a.count == X and a.values == list(range(X)) and a.value == X - 1
    assert a.values_count == X
    assert procedure(rec) == ["1. Fill", "   1.1. Repeat dev.map.pulse.width = <i>"]
    line = _line(rec, "1.1")
    assert line.startswith("   1.1. Repeat dev.map.pulse.width = 0, 1, … 15 -> (16 times, ")
    assert line.endswith(" ms)")


def test_few_values_are_all_shown(dev, rec):
    for i in range(3):
        dev.map.pulse.width = i * 10
    lines = report(rec).splitlines()
    assert any("Repeat dev.map.pulse.width = 0, 10, 20 -> (3 times, " in x for x in lines)


def test_same_values_keep_no_list(dev, rec):
    for _ in range(4):
        dev.map.pulse.width = 7
    (a,) = rec.unscoped
    assert a.count == 4 and a.values is None
    assert "values" not in case_record(rec)["unscoped"][0]


def test_values_start_after_equal_ones(dev, rec):
    for v in (5, 5, 5, 7):
        dev.map.pulse.width = v
    (a,) = rec.unscoped
    assert a.values == [5, 5, 5, 7] and a.values_count == 4 and a.value == 7


def test_mixed_write_and_read_pass(dev, rec):
    with step("Outer"):
        with repeat("Mixed"):
            for i in range(X):
                dev.map.pulse.width = i
                dev.map.adc.value
    (loop,) = rec.steps[0].entries
    assert loop.loop is not None and loop.loop.count == X
    write, read = loop.entries
    assert write.values == list(range(X)) and write.count == 1
    assert read.values is None
    assert procedure(rec) == [
        "1. Outer",
        "   1.1. Mixed (repeat)",
        "      1.1.1. dev.map.pulse.width = <i>",
        "      1.1.2. Read dev.map.adc.value",
    ]
    lines = report(rec).splitlines()
    assert lines[1].startswith("   1.1. Mixed (repeat)   ✓ 16 times")
    assert lines[2] == "      1.1.1. dev.map.pulse.width = 0, 1, … 15"
    assert lines[3].startswith("      1.1.2. Read dev.map.adc.value -> ")
    assert len(lines) == 4


def test_titled_repeat_at_top_level(dev, rec):
    with repeat("Fill the FIFO"):
        for i in range(X):
            dev.map.pulse.width = i
    (s,) = rec.steps
    assert s.loop.count == X and len(s.entries) == 1 and s.entries[0].values == list(range(X))
    lines = report(rec).splitlines()
    assert lines[0].startswith("1. Fill the FIFO (repeat)   ✓ 16 times")
    assert lines[1] == "   1.1. dev.map.pulse.width = 0, 1, … 15"
    assert procedure(rec) == ["1. Fill the FIFO (repeat)", "   1.1. dev.map.pulse.width = <i>"]


def test_untitled_repeat_in_a_step(dev, rec):
    with step("Fill"):
        with repeat(until="FIFO is full"):
            for i in range(X):
                dev.map.pulse.width = i
                dev.map.ctrl.mode = 1
    (rep,) = rec.steps[0].entries
    assert isinstance(rep, Repeat) and rep.count == X
    assert rep.entries[0].values == list(range(X)) and rep.entries[1].values is None
    lines = report(rec).splitlines()
    assert lines[1].startswith("   1.1. Repeat until FIFO is full   16 times, ")
    assert lines[2:] == ["      1.1.1. dev.map.pulse.width = 0, 1, … 15", "      1.1.2. dev.map.ctrl.mode = 1"]


def test_auto_loop_with_a_write_of_one_value(dev, rec):
    with step("Fill"):
        for i in range(4):
            dev.map.ctrl.mode = 1
            dev.map.pulse.width = i
    (rep,) = rec.steps[0].entries
    assert rep.count == 4 and rep.entries[0].values is None and rep.entries[1].values == [0, 1, 2, 3]
    assert "      1.1.2. dev.map.pulse.width = 0, 1, 2, 3" in report(rec).splitlines()


def test_values_inside_each_pass_stay_in_order(dev, rec):
    for i in range(3):
        for j in range(2):
            dev.map.pulse.width = i * 2 + j
        dev.map.adc.value
    (rep,) = rec.unscoped
    write = rep.entries[0]
    assert rep.count == 3 and write.count == 2
    assert write.values == [0, 1, 2, 3, 4, 5] and write.values_count == 6


def test_values_in_record_match_the_schema(dev, rec):
    with step("Fill"):
        for i in range(X):
            dev.map.pulse.width = i
            dev.map.adc.value
    record = run_record([case_record(rec)])
    jsonschema.validate(record, SCHEMA)
    write = record["tests"][0]["steps"][0]["entries"][0]["entries"][0]
    assert write["values"] == list(range(X)) and write["values_count"] == X
    assert write["value"]["concrete"] == X - 1 and write["value"]["symbolic"] == "<i>"
    assert write["value"]["expr"] == "i"


def test_values_are_json_safe(dev, rec):
    for v in (1.5, float("nan"), b"\x01"):
        dev.map.pulse.width = v
    record = run_record([case_record(rec)])
    jsonschema.validate(record, SCHEMA)
    values = record["tests"][0]["unscoped"][0]["values"]
    assert values[0] == 1.5 and values[1] == {"$type": "float", "$repr": "nan"}
    assert values[2]["$type"] == "bytes"
    json.dumps(record, allow_nan=False)


def test_long_sequences_keep_first_and_last(dev, rec):
    for i in range(200):
        dev.map.pulse.width = i
    (a,) = rec.unscoped
    assert a.count == 200 and a.values_count == 200
    assert a.values == [*range(32), *range(168, 200)]
    jsonschema.validate(run_record([case_record(rec)]), SCHEMA)
    assert "Repeat dev.map.pulse.width = 0, 1, … 199 -> (200 times, " in report(rec)


def test_long_sequences_in_a_loop_keep_first_and_last(dev, rec):
    for i in range(100):
        dev.map.pulse.width = i
        dev.map.adc.value
    (rep,) = rec.unscoped
    write = rep.entries[0]
    assert rep.count == 100 and write.values_count == 100
    assert write.values == [*range(32), *range(68, 100)]


def test_markdown_and_html_show_the_sequence(dev, rec):
    with step("Fill"):
        for i in range(X):
            dev.map.pulse.width = i
            dev.map.adc.value
    record = run_record([case_record(rec)])
    md = render(record, "report", "md")
    html = render(record, "report", "html")
    assert "0, 1, … 15" in md and "0, 1, … 15" in html
    assert "<i>" not in md
    assert "&lt;i&gt;" in render(record, "procedure", "html")
    assert "0, 1" not in render(record, "procedure", "md")


def test_failed_check_in_one_pass_stays_visible(dev, rec):
    with step("Fill"):
        for i in range(5):
            dev.map.pulse.width = i
            rec.add_check(Check(text="width ok", passed=i != 3, location=Location(__file__, 1)))
    rep, *rest = rec.steps[0].entries
    assert isinstance(rep, Repeat) and rep.count == 3
    assert rep.entries[0].values == [0, 1, 2]
    # The failed pass and the one after it follow as ordinary entries.
    assert [type(e).__name__ for e in rest] == ["Action", "Check", "Action", "Check"]
    assert [rest[0].value, rest[1].passed, rest[2].value] == [3, False, 4]
    assert rec.steps[0].status == "failed"


def test_a_different_op_breaks_the_loop(dev, rec):
    for i in range(4):
        if i == 2:
            stepdoc.record_action("dev.map.pulse.width", "set", i)
        else:
            stepdoc.record_action("dev.map.pulse.width", "write", i)
    assert [(a.op, a.count, a.values) for a in rec.unscoped] == [
        ("write", 2, [0, 1]),
        ("set", 1, None),
        ("write", 1, None),
    ]


def test_another_line_breaks_the_loop(dev, rec):
    for i in range(4):
        if i < 2:
            dev.map.pulse.width = i
        else:
            dev.map.pulse.width = i
    assert [(a.count, a.values) for a in rec.unscoped] == [(2, [0, 1]), (2, [2, 3])]


def test_collapse_off_keeps_every_write(dev):
    with Recorder(collapse_repeats=False) as rec:
        for i in range(4):
            dev.map.pulse.width = i
    assert [(a.value, a.count, a.values) for a in rec.unscoped] == [(i, 1, None) for i in range(4)]


def test_without_a_site_values_must_be_equal(dev):
    with Recorder(symbolic=False) as rec:
        for i in range(3):
            dev.map.pulse.width = i
        for _ in range(3):
            dev.map.pulse.width = 9
    assert [(a.value, a.count) for a in rec.unscoped] == [(0, 1), (1, 1), (2, 1), (9, 3)]


def test_dry_run_collapses_too(dev):
    with Recorder(dry_run=True) as rec:
        with step("Fill"):
            for i in range(X):
                dev.map.pulse.width = i
                dev.map.adc.value
    (rep,) = rec.steps[0].entries
    assert rep.count == X and rep.entries[0].values == list(range(X))
    assert procedure(rec)[2] == "      1.1.1. dev.map.pulse.width = <i>"


@look_through
def _set_width(dev, w):
    dev.map.pulse.width = w


def test_look_through_helper_from_two_lines_does_not_collapse(dev, rec):
    _set_width(dev, 1)
    _set_width(dev, 2)
    for i in range(3):
        _set_width(dev, i)
    rec.resolve()
    assert [(a.count, a.symbolic_value) for a in rec.unscoped] == [(1, "1"), (1, "2"), (3, "<i>")]
    assert rec.unscoped[2].values == [0, 1, 2]


def test_values_without_source_show_in_the_procedure(dev, rec):
    code = compile(f"for i in range({X}):\n    dev.map.pulse.width = i\n", "<generated>", "exec")
    exec(code, {"dev": dev})
    (a,) = rec.unscoped
    assert a.count == X and a.values == list(range(X))
    assert procedure(rec)[1] == "   - Repeat dev.map.pulse.width = 0, 1, … 15"
    record = case_record(rec)
    assert record["unscoped"][0]["resolved"] is False


def test_requests_with_different_bodies(api, rec):
    with step("Create users"):
        for i in range(5):
            api.post("/users", json={"name": f"u{i}", "age": i})
    (post,) = rec.steps[0].actions
    assert post.count == 5 and [v["age"] for v in post.values] == [0, 1, 2, 3, 4]
    assert procedure(rec)[1].startswith("   1.1. Repeat POST /users  ")
    line = _line(rec, "1.1")
    assert "{'name': 'u0', 'age': 0}, {'name': 'u1', 'age': 1}, … {'name': 'u4', 'age': 4}" in line
    assert "(5 times, " in line


def _set_mode(dev, m):  # an ordinary helper: its line is the write's site
    dev.map.ctrl.mode = m


def _burst(dev, a, b):
    dev.map.pulse.width = a
    dev.map.ctrl.mode = b


def test_plain_helper_from_two_lines_does_not_collapse(dev, rec):
    with step("Configure"):
        _set_mode(dev, 1)
        _set_mode(dev, 2)
    assert [(a.value, a.count, a.values) for a in rec.steps[0].entries] == [(1, 1, None), (2, 1, None)]
    assert procedure(rec)[1:] == ["   1.1. dev.map.ctrl.mode = <m>", "   1.2. dev.map.ctrl.mode = <m>"]


def test_multi_write_helper_from_three_lines_does_not_fold(dev, rec):
    with step("Program"):
        _burst(dev, 1, 2)
        _burst(dev, 3, 4)
        _burst(dev, 5, 6)
    assert [a.value for a in rec.steps[0].entries] == [1, 2, 3, 4, 5, 6]


def test_helper_pulse_in_a_loop_keeps_both_writes(dev, rec):
    with step("Pulse"):
        for i in range(4):
            _set_mode(dev, i + 1)  # start the pulse
            _set_mode(dev, 0)  # end it
            dev.map.adc.value
    (rep,) = rec.steps[0].entries
    assert isinstance(rep, Repeat) and rep.count == 4
    start, end, _ = rep.entries
    assert start.values == [1, 2, 3, 4] and end.values is None and end.value == 0
    assert procedure(rec)[2:4] == [
        "      1.1.1. dev.map.ctrl.mode = <m>",
        "      1.1.2. dev.map.ctrl.mode = <m>",
    ]


def test_helper_in_a_loop_collapses(dev, rec):
    for i in range(X):
        _set_mode(dev, i)
    (a,) = rec.unscoped
    assert a.count == X and a.values == list(range(X))


def test_two_writes_on_one_line_stay_two(dev, rec):
    with step("S"):
        dev.map.pulse.width = 1; dev.map.pulse.width = 2  # noqa: E702
    assert [(a.value, a.count) for a in rec.steps[0].entries] == [(1, 1), (2, 1)]
    assert procedure(rec)[1:] == ["   1.1. dev.map.pulse.width = 1", "   1.2. dev.map.pulse.width = 2"]


def test_same_value_twice_on_one_line_still_collapses(dev, rec):
    dev.map.pulse.width = 3; dev.map.pulse.width = 3  # noqa: E702
    assert [(a.value, a.count) for a in rec.unscoped] == [(3, 2)]


def test_repeated_write_in_each_pass_says_per_pass(dev, rec):
    with step("S"):
        for i in range(3):
            for j in range(4):
                dev.map.pulse.width = j + 10 * i
            dev.map.adc.value
    (rep,) = rec.steps[0].entries
    write = rep.entries[0]
    assert rep.count == 3 and write.count == 4 and write.values_count == 12
    assert _line(rec, "1.1.1") == (
        "      1.1.1. Repeat dev.map.pulse.width = 0, 1, … 23 -> (4 times per pass, 12 values)"
    )


def test_unfinished_last_pass_shows_its_values(dev, rec):
    with step("S"):
        with repeat("L"):
            for i in range(5):
                dev.map.pulse.width = i
                if i == 4:
                    break
                dev.map.adc.value
    (loop,) = rec.steps[0].entries
    assert loop.loop.count == 4 and loop.entries[0].values_count == 5
    assert _line(rec, "1.1.1") == "      1.1.1. dev.map.pulse.width = 0, 1, … 4 -> (5 values)"


def test_a_pass_that_skips_its_read_is_not_counted_as_a_pass(dev, rec):
    """Pass 3 skips the read: its write and pass 4's are next to each other. They
    are not one pass writing twice, so the loop does not take them in, and the
    count stays true to what ran."""
    with step("Fill"):
        for i in range(6):
            dev.map.pulse.width = i
            if i != 3:
                dev.map.adc.value
    entries = rec.steps[0].entries
    loop = entries[0]
    assert isinstance(loop, Repeat) and loop.count == 3
    assert loop.entries[0].values == [0, 1, 2]
    written = []
    for e in entries:
        for a in (e.entries if isinstance(e, Repeat) else [e]):
            if a.target == "dev.map.pulse.width":
                written += a.values or [a.value] * a.count
    passes = sum(e.count for e in entries if isinstance(e, Repeat))
    assert written[: 3] == [0, 1, 2] and sorted(written) == list(range(6))
    assert passes < 6
