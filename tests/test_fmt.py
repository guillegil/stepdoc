"""FMT-1: `code` spans in step titles, and fmt.mono() to write them."""

import pytest

from stepdoc import Recorder, fmt, step
from stepdoc.core.record import case_record, run_record
from stepdoc.renderers import render


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("dut.reg1 = 1", "`dut.reg1 = 1`"),
        ("a`b", "``a`b``"),
        ("`edge", "`` `edge ``"),
        ("x``y", "```x``y```"),
        (42, "`42`"),
        ("", ""),
    ],
)
def test_mono_writes_a_span_that_round_trips(text, expected):
    assert fmt.mono(text) == expected
    if expected:
        assert fmt.spans(expected) == [(str(text), True)]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Write into `dut.reg1 = 1`", [("Write into ", False), ("dut.reg1 = 1", True)]),
        ("`a` and `b`", [("a", True), (" and ", False), ("b", True)]),
        ("no code", [("no code", False)]),
        ("a lone ` stays", [("a lone ` stays", False)]),
        ("``a`` then `", [("a", True), (" then `", False)]),
        ("empty `` span", [("empty `` span", False)]),
        ("blank ` ` span", [("blank ` ` span", False)]),
    ],
)
def test_spans_follow_markdown_code_spans(title, expected):
    assert fmt.spans(title) == expected


def _record(title):
    with Recorder() as rec:
        with step(title):
            pass
    return run_record([case_record(rec, test_id="t.py::test_x", outcome="passed")])


def test_html_shows_spans_as_code_and_escapes_them():
    record = _record(f"Write {fmt.mono('<reg> = 1')} now")
    for doc in ("procedure", "report"):
        page = render(record, doc, "html")
        assert "Write <code>&lt;reg&gt; = 1</code> now" in page


def test_markdown_and_text_keep_the_title_as_written():
    record = _record("Write `dut.reg1 = 1`")
    assert "**1. Write `dut.reg1 = 1`**" in render(record, "procedure", "md")
    assert "1. Write `dut.reg1 = 1`" in render(record, "procedure", "text")
