"""DOC-1: a test's docstring as its description, objective and acceptance criteria."""

import json
from pathlib import Path

import jsonschema
import pytest

from stepdoc import Recorder, step
from stepdoc.cli import main
from stepdoc.core.docstring import parse_docstring
from stepdoc.core.record import case_record, load, run_record
from stepdoc.renderers import render
from stepdoc.renderers.common import Block, doc_blocks

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden"
SCHEMA = json.loads((HERE.parent / "src/stepdoc/schema/run-record.schema.json").read_text())

EXAMPLE = '''Fill the CMD FIFO until the TX flag sets.

    Objective:
        Confirm the FIFO accepts its full depth before it reports full.

    Acceptance criteria:
        1. The TX flag sets after exactly 500 writes.
        2. No write is lost or reported as an error.
    '''

EXAMPLE_DOC = {
    "description": "Fill the CMD FIFO until the TX flag sets.",
    "objective": "Confirm the FIFO accepts its full depth before it reports full.",
    "acceptance": "1. The TX flag sets after exactly 500 writes.\n2. No write is lost or reported as an error.",
}


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def test_example():
    assert parse_docstring(EXAMPLE) == EXAMPLE_DOC


@pytest.mark.parametrize("text", [None, "", "   ", "\n\n  \n"])
def test_no_docstring(text):
    assert parse_docstring(text) is None


def test_summary_only():
    assert parse_docstring("Reset the device.") == {"description": "Reset the device."}


def test_summary_and_paragraphs_keep_their_breaks():
    doc = parse_docstring("""Reset the device.

        The reset line is held low
        for 10 ms.
        """)
    assert doc == {"description": "Reset the device.\n\nThe reset line is held low\nfor 10 ms."}


def test_inline_heading_text():
    doc = parse_docstring("""Summary.

    Objective: Show that the FIFO fills.
    Acceptance criteria: The flag sets after 500 writes.
    """)
    assert doc == {
        "description": "Summary.",
        "objective": "Show that the FIFO fills.",
        "acceptance": "The flag sets after 500 writes.",
    }


def test_inline_heading_text_with_continuation():
    doc = parse_docstring("""Objective: Show that
        the FIFO fills.
    """)
    assert doc == {"objective": "Show that\nthe FIFO fills."}


@pytest.mark.parametrize("heading, key", [
    ("OBJECTIVE:", "objective"),
    ("objectives:", "objective"),
    ("Objective :", "objective"),
    ("Acceptance Criteria:", "acceptance"),
    ("acceptance criterion:", "acceptance"),
    ("Pass criteria:", "acceptance"),
    ("Pass/Fail criteria:", "acceptance"),
    ("Pass / fail criterion:", "acceptance"),
    ("Acceptance/Pass criteria:", "acceptance"),
    ("Acceptance/pass criterion:", "acceptance"),
])
def test_heading_variants(heading, key):
    assert parse_docstring(f"Summary.\n\n{heading}\n    Text.\n") == {"description": "Summary.", key: "Text."}


@pytest.mark.parametrize("line", ["Objective", "Pass criteria", "Objective is clear:", "Acceptance:", "criteria:"])
def test_not_headings(line):
    assert parse_docstring(f"Summary.\n\n{line}\n") == {"description": f"Summary.\n\n{line}"}


def test_unknown_headings_stay_prose():
    doc = parse_docstring("""Summary.

    Setup:
        Power on the board.

    Objective:
        Show it.
        Notes:
        none
    """)
    assert doc == {
        "description": "Summary.\n\nSetup:\n    Power on the board.",
        "objective": "Show it.\nNotes:\nnone",
    }


def test_indented_heading_is_prose():
    doc = parse_docstring("Summary.\n\n    - first\n      Objective: not a heading\n")
    assert doc == {"description": "Summary.\n\n- first\n  Objective: not a heading"}


def test_description_heading_written_anyway():
    assert parse_docstring("""
    Description:
        Fill the FIFO.

    Objective: Show it.
    """) == {"description": "Fill the FIFO.", "objective": "Show it."}
    assert parse_docstring("""Summary.

    Description:
        More words.
    """) == {"description": "Summary.\n\nMore words."}


def test_empty_sections_are_left_out():
    assert parse_docstring("Objective:\n\nAcceptance criteria:\n") is None
    assert parse_docstring("Summary.\n\nObjective:\n") == {"description": "Summary."}


def test_crlf_and_tabs():
    text = "Summary.\r\n\r\n\tObjective:\r\n\t\tShow it.\r\n\tAcceptance criteria:\r\n\t\t- one\r\n\t\t- two\r\n"
    assert parse_docstring(text) == {"description": "Summary.", "objective": "Show it.", "acceptance": "- one\n- two"}


def test_lists_keep_their_markers():
    doc = parse_docstring("""Summary.

    Acceptance criteria:
        * first
          goes on
        * second
    """)
    assert doc == {"description": "Summary.", "acceptance": "* first\n  goes on\n* second"}


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #


def test_blocks_paragraphs():
    assert doc_blocks("One\ntwo.\n\nThree.") == [Block("p", ["One two."], []), Block("p", ["Three."], [])]


def test_blocks_bullets_with_continuations():
    assert doc_blocks("- first\n  goes on\n* second\nlazy\n\n  after a gap\n\nAfter.") == [
        Block("ul", ["first goes on", "second lazy after a gap"], []),
        Block("p", ["After."], []),
    ]


def test_blocks_numbers_keep_the_authors():
    assert doc_blocks("Intro:\n1) one\n3) three\n\n4. four") == [
        Block("p", ["Intro:"], []),
        Block("ol", ["one", "three", "four"], [1, 3, 4]),
    ]


def test_blocks_sentence():
    assert doc_blocks("The flag sets after\n500 writes.") == [Block("p", ["The flag sets after 500 writes."], [])]


@pytest.mark.parametrize("gap", ["", "\n"])
def test_blocks_indented_list_after_an_intro(gap):
    assert doc_blocks(f"The test passes when:\n{gap}    1. a\n    2. b") == [
        Block("p", ["The test passes when:"], []),
        Block("ol", ["a", "b"], [1, 2]),
    ]


def test_blocks_right_aligned_numbers():
    assert doc_blocks(" 9. nine\n10. ten") == [Block("ol", ["nine", "ten"], [9, 10])]


def test_blocks_nested_items_stay_items():
    assert doc_blocks("- a\n    - sub\n      more\n- b") == [Block("ul", ["a", "sub more", "b"], [])]


def test_blocks_wrapped_number_continues_a_paragraph():
    assert doc_blocks("Write words until the count reaches\n512. Then read the flag.") == [
        Block("p", ["Write words until the count reaches 512. Then read the flag."], [])
    ]


@pytest.mark.parametrize("after", ["\n\nAll of them must hold.", "\nNotes:\n    something else"])
def test_list_followed_by_section_prose(after):
    doc = parse_docstring(
        "S.\n\n    Acceptance criteria:\n        - first\n        - second\n          continues here"
        + after.replace("\n", "\n    ")
    )
    prose = "All of them must hold." if "All" in after else "Notes: something else"
    assert doc_blocks(doc["acceptance"]) == [
        Block("ul", ["first", "second continues here"], []),
        Block("p", [prose], []),
    ]
    md = render(documented_run(doc), "procedure", "md")
    assert f"**Acceptance criteria**\n\n- first\n- second continues here\n\n{prose}\n" in md
    page = render(documented_run(doc), "procedure", "html")
    assert f"<ul><li>first</li><li>second continues here</li></ul><p>{prose}</p>" in page
    text = render(documented_run(doc), "procedure", "text")
    assert f"    - first\n    - second continues here\n    {prose}\n" in text


def test_markdown_escapes_a_leading_number():
    from stepdoc.renderers.markdown import _words

    assert _words("1. a 2. b") == "1\\. a 2. b"
    assert _words("3) c") == "3\\) c"
    assert _words("12 items") == "12 items"


# --------------------------------------------------------------------------- #
# Record and hook
# --------------------------------------------------------------------------- #


def test_case_record_doc_present_and_absent():
    with Recorder() as rec:
        pass
    assert case_record(rec, doc=EXAMPLE_DOC)["doc"] == EXAMPLE_DOC
    assert "doc" not in case_record(rec)
    assert "doc" not in case_record(rec, doc={})


PLUGIN = '''
import json
import pytest

SEEN = []

@pytest.hookimpl(optionalhook=True)
def pytest_stepdoc_test_doc(item, doc):
    SEEN.append((item.nodeid, doc))

def pytest_sessionfinish(session):
    with open("seen.json", "w") as f:
        json.dump(SEEN, f)
'''

TESTS = '''
import pytest

@pytest.mark.parametrize("channel", [1, 2])
def test_documented(channel):
    """Fill the FIFO.

    Objective: Show it.

    Acceptance criteria:
        - It fills.
    """

def test_plain():
    pass

class TestThings:
    """A class docstring is not a test's."""

    def test_method(self):
        pass
'''


def test_plugin_records_doc_and_calls_hook(pytester):
    pytester.makeconftest(PLUGIN)
    pytester.makepyfile(test_doc=TESTS)
    result = pytester.runpytest("-p", "no:cacheprovider", "--stepdoc-record=run.json", "--stepdoc-procedure=p.md")
    result.assert_outcomes(passed=4)
    record = json.loads((pytester.path / "run.json").read_text())
    jsonschema.validate(record, SCHEMA)
    assert record["schema_version"] == "0.5"
    docs = {t["id"]: t.get("doc") for t in record["tests"]}
    doc = {"description": "Fill the FIFO.", "objective": "Show it.", "acceptance": "- It fills."}
    assert docs == {
        "test_doc.py::test_documented[1]": doc,
        "test_doc.py::test_documented[2]": doc,
        "test_doc.py::test_plain": None,
        "test_doc.py::TestThings::test_method": None,
    }
    seen = json.loads((pytester.path / "seen.json").read_text())
    assert seen == [["test_doc.py::test_documented[1]", doc], ["test_doc.py::test_documented[2]", doc]]
    procedure = (pytester.path / "p.md").read_text()
    assert procedure.count("**Objective.** Show it.") == 1
    assert "A class docstring" not in procedure


def test_hook_not_called_without_recording(pytester):
    pytester.makeconftest(PLUGIN)
    pytester.makepyfile(test_doc=TESTS)
    pytester.runpytest("-p", "no:cacheprovider").assert_outcomes(passed=4)
    assert json.loads((pytester.path / "seen.json").read_text()) == []


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #


def documented_run(doc):
    cases = []
    for level in (1, 2):
        with Recorder() as rec:
            with step("Fill the FIFO"):
                pass
        cases.append(case_record(rec, test_id=f"t.py::test_fill[{level}]", procedure_id="t.py::test_fill",
                                 case=str(level), params={"level": level}, outcome="passed",
                                 doc=doc if level == 2 else None))
    return run_record(cases)


def test_markdown_procedure():
    md = render(documented_run(EXAMPLE_DOC), "procedure", "md")
    expected = """_t.py::test_fill · 2 cases_

Fill the CMD FIFO until the TX flag sets.

**Objective.** Confirm the FIFO accepts its full depth before it reports full.

**Acceptance criteria**

1. The TX flag sets after exactly 500 writes.
2. No write is lost or reported as an error.

**Parameters**
"""
    assert expected in md


def test_markdown_report_per_case():
    md = render(documented_run(EXAMPLE_DOC), "report", "md")
    first, second = md.split("## test_fill[1]")[1].split("## test_fill[2]")
    assert "Objective" not in first
    assert second.index("Fill the CMD FIFO") < second.index("**Objective.**") < second.index("Parameters:")


def test_markdown_escapes_prose_and_keeps_code():
    doc = {
        "description": "Writes `dev.reg<ch>` *twice* <b>\n\n# not a heading",
        "objective": "First.\n\nSecond with `code`.",
        "acceptance": "The flag `tx` sets.",
    }
    md = render(documented_run(doc), "procedure", "md")
    assert "Writes `dev.reg<ch>` \\*twice\\* \\<b\\>\n\n\\# not a heading\n" in md
    assert "**Objective**\n\nFirst.\n\nSecond with `code`.\n" in md
    assert "**Acceptance criteria**\n\nThe flag `tx` sets.\n" in md


def test_html():
    doc = dict(EXAMPLE_DOC, description="Uses `<ch>` & <b>", acceptance="- one\n- two\n\n3) three\n5) five")
    page = render(documented_run(doc), "procedure", "html")
    assert '<div class="doc"><p>Uses <code>&lt;ch&gt;</code> &amp; &lt;b&gt;</p>' in page
    assert '<p><span class="doc-title">Objective.</span> Confirm' in page
    assert '<p class="doc-title">Acceptance criteria</p><ul><li>one</li><li>two</li></ul>' in page
    assert '<ol><li value="3">three</li><li value="5">five</li></ol></div>' in page
    assert ".doc-title" in page
    report = render(documented_run(EXAMPLE_DOC), "report", "html")
    assert report.count('<div class="doc">') == 1
    assert "<ol><li>The TX flag" in report


def test_text():
    text = render(documented_run(EXAMPLE_DOC), "procedure", "text")
    assert text.startswith("""t.py::test_fill
  Fill the CMD FIFO until the TX flag sets.
  Objective: Confirm the FIFO accepts its full depth before it reports full.
  Acceptance criteria:
    1. The TX flag sets after exactly 500 writes.
    2. No write is lost or reported as an error.
Parameters:
""")
    report = render(documented_run(EXAMPLE_DOC), "report", "text")
    assert report.count("Objective:") == 1


@pytest.mark.parametrize("fmt", ["md", "html", "text"])
@pytest.mark.parametrize("doc", ["procedure", "report"])
def test_records_without_doc_render_as_before(doc, fmt, tmp_path, capsys):
    """A 0.4 record renders byte for byte as stepdoc rendered it before DOC-1."""
    assert main(["render", str(GOLDEN / "record-0.4.json"), "--doc", doc, "--format", fmt]) == 0
    assert capsys.readouterr().out == (GOLDEN / f"{doc}.{fmt}").read_text(encoding="utf-8")


def test_cli_renders_doc(tmp_path, capsys):
    path = tmp_path / "run.json"
    path.write_text(json.dumps(documented_run(EXAMPLE_DOC)))
    assert load(path)["schema_version"] == "0.5"
    assert main(["render", str(path)]) == 0
    assert "**Objective.** Confirm" in capsys.readouterr().out
