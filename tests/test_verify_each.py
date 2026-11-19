"""``all_satisfy`` as one line (CHK-11), with the real pytest-verifier.

One check per item that reads the same for every item is folded into one line,
``'Index error': for each w in waveforms, w & INDEX_ERROR_BIT == 0``; the report
says how many passed and lists only the items that failed. Skipped where
pytest-verifier is not installed or does not name the items (before 0.11).
"""

import pytest

from tests.test_verify_operands import numbered, record_of, run

pytest_verifier = pytest.importorskip("pytest_verifier", reason="pytest-verifier is not installed")
if "item_name" not in getattr(pytest_verifier, "CheckDescriptor").__annotations__:
    pytest.skip("this pytest-verifier does not name all_satisfy's items", allow_module_level=True)

pytest_plugins = ["pytester"]

WAVEFORMS = '''
import pytest
from stepdoc import step

INDEX_ERROR_BIT = 0x40


@pytest.mark.parametrize("bad", [(), (3, 9)])
def test_waveforms(verify, bad):
    waveforms = [0x40 if i in bad else 0x01 for i in range(12)]
    with step("Verify the Index Error bit is 0"):
        verify.all_satisfy(
            waveforms,
            lambda w: verify.equal(w & INDEX_ERROR_BIT, 0, name="Index error"),
            name="Index Error is 0, every waveform",
        )
'''


@pytest.fixture
def project(pytester):
    pytester.makeini("[pytest]\n")
    return pytester


def test_one_line_in_the_procedure_and_failed_items_in_the_report(project):
    project.makepyfile(test_waveforms=WAVEFORMS)
    run(project).assert_outcomes(passed=1, failed=1)
    line = "Verify 'Index Error is 0, every waveform': for each w in waveforms, w & INDEX_ERROR_BIT == 0"
    procedure = numbered(project, "procedure.txt")
    assert procedure == ["1. Verify the Index Error bit is 0", f"   1.1. {line}"]
    report = numbered(project, "report.txt")
    assert f"   1.1. {line} -> 12 of 12 passed   ✓" in report
    assert f"   1.1. {line} -> 2 of 12 failed   ✗" in report
    assert "      - Verify item [3]: w & INDEX_ERROR_BIT == 0 -> got 64   ✗" in report
    assert "      - Verify item [9]: w & INDEX_ERROR_BIT == 0 -> got 64   ✗" in report
    assert len([x for x in report if "item [" in x]) == 2
    passed, failed = (t["steps"][0]["entries"][0] for t in record_of(project)["tests"])
    assert passed["each"] == {"count": 12, "failed": 0, "item": "w", "items": "waveforms", "values": [0] * 12}
    assert passed["children"] == [] and passed["label"] == "Index Error is 0, every waveform"
    assert failed["each"]["failed"] == 2 and [c["index"] for c in failed["children"]] == [3, 9]
    assert failed["each"]["values"][3] == 64


def test_markdown_and_html(project):
    project.makepyfile(test_waveforms=WAVEFORMS)
    args = ("--stepdoc-procedure=procedure.md", "--stepdoc-report=report.html")
    project.runpytest_subprocess("-p", "no:cacheprovider", *args).assert_outcomes(passed=1, failed=1)
    md = (project.path / "procedure.md").read_text(encoding="utf-8")
    assert "for each w in waveforms, w & INDEX_ERROR_BIT == 0" in md
    assert "item [" not in md
    html = (project.path / "report.html").read_text(encoding="utf-8")
    assert "12 of 12 passed" in html and "2 of 12 failed" in html and "item [3]:" in html


def test_items_that_read_differently_stay_listed(project):
    project.makepyfile(
        test_mixed='''
        from stepdoc import step


        def test_mixed(verify):
            with step("Mixed"):
                verify.all_satisfy(
                    [1, 2],
                    lambda v: verify.equal(v, 1, name="one") if v == 1 else verify.greater(v, 0, name="pos"),
                    name="Mixed checks",
                )
        '''
    )
    run(project).assert_outcomes(passed=1)
    report = numbered(project, "report.txt")
    assert report[1].startswith("   1.1. Verify all items in 'Mixed checks' satisfy condition")
    assert [x.strip() for x in report[2:]] == [
        "- Verify 'one': v == 1 -> got 1   ✓", "- Verify 'pos': v > 0 -> got 2   ✓",
    ]
    assert "each" not in record_of(project)["tests"][0]["steps"][0]["entries"][0]


def test_without_a_lambda_the_item_is_unnamed(project):
    project.makepyfile(
        test_partial='''
        import functools
        from stepdoc import step


        def test_partial(verify):
            levels = [1, 2, 3]
            with step("Levels"):
                verify.all_satisfy(levels, functools.partial(verify.greater, threshold=0), name="Positive")
        '''
    )
    run(project).assert_outcomes(passed=1)
    assert numbered(project, "procedure.txt")[1].startswith(
        "   1.1. Verify 'Positive': for each item in levels, "
    )
