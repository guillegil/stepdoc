"""``note=`` on steps and repeat blocks: a sentence for the reader under the line."""

import json
from pathlib import Path

import jsonschema
import pytest

from stepdoc import repeat, step
from stepdoc.core.record import case_record
from stepdoc.renderers import html, markdown
from tests.helpers import procedure, report

SCHEMA = json.loads(
    (Path(__file__).parent.parent / "src" / "stepdoc" / "schema" / "run-record.schema.json").read_text()
)


def _validate(record):
    jsonschema.validate({"schema_version": "0.4", "generator": {"name": "stepdoc", "version": "x",
                         "python": "3"}, "created_at": "", "dry_run": False, "tests": [record]}, SCHEMA)


def test_step_note_is_shown_under_the_step(dev, rec):
    with step("Fill the CMD FIFO", note="The FIFO is 512 words; 12 are reserved."):
        dev.map.ctrl.enable = 1
    lines = procedure(rec)
    assert lines[:2] == ["1. Fill the CMD FIFO", "   Note: The FIFO is 512 words; 12 are reserved."]
    assert lines[2].startswith("   1.1. ")
    assert "   Note: The FIFO is 512 words; 12 are reserved." in report(rec).splitlines()
    record = case_record(rec)
    assert record["steps"][0]["note"] == "The FIFO is 512 words; 12 are reserved."
    _validate(record)


def test_without_a_note_the_record_has_no_note_field(rec):
    with step("Plain"):
        pass
    assert "note" not in case_record(rec)["steps"][0]


def test_repeat_note(dev, rec):
    with step("Empty FIFO"):
        with repeat("Read until full", until="tx = 1", note="write_count counts the reads"):
            for _ in range(3):
                dev.map.ctrl.enable = 1
    lines = procedure(rec)
    i = lines.index("   1.1. Read until full (repeat until tx = 1)")
    assert lines[i + 1] == "      Note: write_count counts the reads"
    record = case_record(rec)
    assert record["steps"][0]["entries"][0]["note"] == "write_count counts the reads"
    _validate(record)


def test_multi_line_note_is_dedented(rec):
    with step("Calibrate", note="""
            First line.
            Second line.
            """):
        pass
    assert procedure(rec) == ["1. Calibrate", "   Note: First line.", "         Second line."]


def test_note_on_leaf_bare_and_decorated_steps(rec):
    step("Leaf", check=True, note="leaf note")
    step("Bare", note="bare note")

    @step("Helper", note="helper note")
    def helper():
        pass

    helper()
    notes = [s.get("note") for s in case_record(rec)["steps"]]
    assert notes == ["leaf note", "bare note", "helper note"]


def test_blank_note_is_dropped_and_non_text_is_refused(rec):
    with step("Blank", note="   "):
        pass
    assert "note" not in case_record(rec)["steps"][0]
    with pytest.raises(TypeError, match="note must be a str"):
        step("Bad", note=3)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        repeat(note=1)  # type: ignore[arg-type]


def test_note_in_markdown_and_html(dev, rec):
    with step("Fill", note="Why: <reserved> words"):
        dev.map.ctrl.enable = 1
    record = {"schema_version": "0.4", "tests": [case_record(rec)]}
    md = markdown.render_procedure(record)
    assert "  > Why: <reserved> words" in md
    page = html.render_procedure(record)
    assert '<div class="remark">Why: &lt;reserved&gt; words</div>' in page


def test_titled_repeat_outside_a_step_is_a_step(dev, rec):
    with step("Turn off"):
        dev.map.ctrl.enable = 0
    with repeat("Fill the FIFO", until="tx = 1", counter="write_count", note="512 words, 12 reserved"):
        write_count = 0
        while write_count < 3:
            dev.map.ctrl.enable = 1
            write_count += 1
    with step("Check"):
        pass
    assert procedure(rec) == [
        "1. Turn off",
        "   1.1. dev.map.ctrl.enable = 0",
        "2. Fill the FIFO (repeat until tx = 1, counting write_count)",
        "   Note: 512 words, 12 reserved",
        "   2.1. dev.map.ctrl.enable = 1",
        "3. Check",
    ]
    assert "2. Fill the FIFO (repeat until tx = 1, counting write_count)   ✓ 3 times" in report(rec)
    record = case_record(rec)
    assert [s["title"] for s in record["steps"]] == ["Turn off", "Fill the FIFO", "Check"]
    loop = record["steps"][1]
    assert loop["status"] == "passed" and loop["note"] == "512 words, 12 reserved"
    assert loop["loop"]["until"] == "tx = 1" and loop["loop"]["count"] == 3
    assert loop["loop"]["counter"] == "write_count"
    assert [e["type"] for e in loop["entries"]] == ["action"]
    assert record["unscoped"] == []
    _validate(record)


def test_titled_repeat_inside_a_step_is_a_child_step(dev, rec):
    with step("Empty FIFO"):
        dev.map.ctrl.enable = 0
        with repeat("Drain", until="empty"):
            for _ in range(2):
                dev.map.ctrl.enable = 1
    assert procedure(rec) == [
        "1. Empty FIFO",
        "   1.1. dev.map.ctrl.enable = 0",
        "   1.2. Drain (repeat until empty)",
        "      1.2.1. dev.map.ctrl.enable = 1",
    ]
    (child,) = rec.steps[0].children
    assert child.title == "Drain" and child.loop is not None and child.loop.count == 2


def test_untitled_repeat_outside_a_step_stays_an_entry(dev, rec):
    with repeat(until="done"):
        for _ in range(2):
            dev.map.ctrl.enable = 1
    record = case_record(rec)
    assert record["steps"] == [] and record["unscoped"][0]["type"] == "repeat"


def test_failure_in_a_top_level_repeat_fails_its_step(dev, rec):
    with pytest.raises(RuntimeError):
        with repeat("Fill"):
            dev.map.ctrl.enable = 1
            raise RuntimeError("bus timeout")
    (loop,) = case_record(rec)["steps"]
    assert loop["status"] == "error" and loop["error"] == "RuntimeError: bus timeout"


def test_counter_with_a_step_size(dev, rec):
    with repeat("Fill", until="full", counter="byte_count", inc=4):
        for _ in range(2):
            dev.map.ctrl.enable = 1
    with step("Outer"):
        with repeat(until="done", counter="n"):
            for _ in range(2):
                dev.map.ctrl.enable = 1
    lines = procedure(rec)
    assert lines[0] == "1. Fill (repeat until full, byte_count +4 each pass)"
    assert "   2.1. Repeat until done, counting n" in lines
    record = case_record(rec)
    assert record["steps"][0]["loop"]["inc"] == 4
    assert record["steps"][1]["entries"][0]["counter"] == "n"
    _validate(record)


def test_counter_and_inc_are_checked():
    with pytest.raises(TypeError, match="give counter= too"):
        repeat("Fill", inc=2)
    with pytest.raises(TypeError, match="inc must be a number"):
        repeat("Fill", counter="n", inc="2")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="counter must be"):
        repeat("Fill", counter=3)  # type: ignore[arg-type]
