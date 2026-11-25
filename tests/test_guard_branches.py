"""Every branch of a guarded check, worded from its source (CHK-12).

pytest-verifier's ``guard`` records the branch it took; a lazy check of another
branch is never built. The adapter reads every branch from the ``guard(...)``
call, so the procedure lists them all, in every format, and the generic
procedure is one for every case. Records are built from descriptors by hand, so
these tests run without pytest-verifier; the last one runs it.
"""

import json
import textwrap

import jsonschema
import pytest

from stepdoc import step
from stepdoc.adapters.verify import to_check
from stepdoc.core import Recorder, attach_check, case_record, run_record
from stepdoc.renderers import render
from stepdoc.renderers.generic import render_generic
from tests.test_adapters import SCHEMA
from tests.test_generic import SCHEMA as GENERIC_SCHEMA

pytest_plugins = ["pytester"]

SOURCE = '''\
MAX = 70


def test_speed(verify, mode, limit):
    verify.guard(
        [
            (mode == "fast", "fast mode", lambda: verify.greater_equal(speed, limit, name="Speed", units="rpm")),
            (lambda: mode == "slow", "slow mode", lambda: verify.between(speed, 30, MAX, name="Speed")),
            (mode == "warm", "warm", lambda: verify.approx(speed, 40, abs_tol=2, rel_tol=0.05)),
            (mode == "off", "off", lambda: verify.is_none(dev.speed, name="Idle")),
            (mode == "boot", "boot", lambda: verify.equal(dev.state, "READY", expected_name="ready")),
            (mode == "odd", "odd", lambda: verify.equal(**checks)),
        ],
        default=lambda: verify.is_true(speed == 40),
        name="Speed for the mode",
    )
'''

MODES = ["fast", "slow", "warm", "off", "boot", "odd"]


def guard(mode):
    """What pytest-verifier records for ``SOURCE``: only the taken branch built."""
    index = MODES.index(mode) if mode in MODES else None
    built = {
        "check_type": "greater_equal",
        "name": "Speed",
        "description": "Verify 'Speed' >= 100rpm",
        "actual": 120,
        "passed": True,
        "phase": "call",
        "location": "test_speed.py:7",
    }
    default = {
        "check_type": "true",
        "name": "speed == 40",
        "auto_name": True,
        "description": "Verify 'speed == 40' is True",
        "actual": True,
        "passed": True,
        "phase": "call",
        "location": "test_speed.py:14",
    }
    return {
        "check_type": "guard",
        "name": "Speed for the mode",
        "description": "Verify 'Speed for the mode' [guarded]",
        "matched_index": index,
        "branches": [
            {
                "condition": None if index is not None and i > index else m == mode,
                "label": m if i > 1 else f"{m} mode",
                "check": built if i == index == 0 else None,
            }
            for i, m in enumerate(MODES)
        ],
        "default": default if index is None else None,
        "passed": True,
        "phase": "call",
        "location": "test_speed.py:5",
    }


def record_for(tmp_path, modes=("fast", "idle")):
    (tmp_path / "test_speed.py").write_text(SOURCE)
    cases = []
    for mode in modes:
        with Recorder() as rec:
            with step("Check the speed"):
                attach_check(to_check(guard(mode), str(tmp_path), frozenset({"mode", "limit"})))
        cases.append(case_record(rec, root=str(tmp_path), test_id=f"test_speed.py::test_speed[{mode}]",
                                 procedure_id="test_speed.py::test_speed", case=mode,
                                 params={"mode": mode, "limit": 100}, outcome="passed"))
    return run_record(cases)


WORDED = [
    "If mode == \"fast\" (fast mode): Verify 'Speed': speed >= <limit> rpm",
    "Else if mode == \"slow\" (slow mode): Verify 'Speed': speed ∈ [30, MAX]",
    "Else if mode == \"warm\" (warm): Verify speed == 40 ± 2 (abs) ± 5% (rel)",
    "Else if mode == \"off\" (off): Verify 'Idle': dev.speed is None",
    "Else if mode == \"boot\" (boot): Verify dev.state == <ready>",
    "Else if mode == \"odd\" (odd): Verify verify.equal(**checks)",
    "Otherwise: Verify speed == 40 is True",
]


def test_every_branch_is_worded_from_its_source(tmp_path):
    record = record_for(tmp_path)
    jsonschema.validate(record, SCHEMA)
    branches = record["tests"][1]["steps"][0]["entries"][0]["branches"]  # idle: none built but the default
    assert [b["check"] is not None for b in branches] == [False] * 6 + [True]
    assert [(b.get("wording") or {}).get("text") for b in branches] == [
        "'Speed': speed >= <limit> rpm",
        "'Speed': speed ∈ [30, MAX]",
        "speed == 40 ± 2 (abs) ± 5% (rel)",
        "'Idle': dev.speed is None",
        "dev.state == <ready>",
        None,  # **kwargs: only the built check knows what it checks
        "speed == 40 is True",
    ]
    assert branches[5]["source"] == "verify.equal(**checks)"
    assert all(b["wording"]["passed"] is None for b in branches if b.get("wording"))


def test_text_procedure_lists_every_branch_once(tmp_path):
    text = render(record_for(tmp_path), "procedure", "text")
    lines = [line.strip() for line in text.splitlines()]
    assert "1.1. Verify 'Speed for the mode', depending on the condition:" in lines
    assert [line[2:] for line in lines if line.startswith("- ")] == WORDED
    assert "Variant" not in text and "[→" not in text


def test_markdown_and_html_procedures(tmp_path):
    record = record_for(tmp_path)
    md = render(record, "procedure", "md")
    assert "- If `mode == \"fast\"` (fast mode): Verify 'Speed': `speed >= <limit> rpm`" in md
    assert "- Otherwise: Verify `speed == 40 is True`" in md
    html = render(record, "procedure", "html")
    assert '<span class="branch">Else if <code>mode == &quot;slow&quot;</code> (slow mode):</span>' in html
    assert ".line.not-run" in html


def test_report_marks_the_branch_taken(tmp_path):
    report = render(record_for(tmp_path), "report", "text")
    fast, idle = report.split("\n\n")[:2]
    assert "- If mode == \"fast\" (fast mode): Verify 'Speed': speed >= 100rpm -> got 120   ✓" in fast
    assert "- Else if mode == \"slow\" (slow mode): Verify 'Speed': speed ∈ [30, MAX]   not evaluated" in fast
    assert "- If mode == \"fast\" (fast mode): Verify 'Speed': speed >= <limit> rpm   condition false" in idle
    assert "- Otherwise: Verify speed == 40 is True -> got True   ✓" in idle
    html = render(record_for(tmp_path), "report", "html")
    assert html.count('class="line not-run"') == 6 + 6


def test_generic_json_has_the_branches(tmp_path):
    doc = json.loads(render_generic(record_for(tmp_path)))
    jsonschema.validate(doc, GENERIC_SCHEMA)
    test = doc["tests"][0]
    assert "variants" not in test and test["cases"] == 2
    check = test["steps"][0]["steps"][0]
    assert check["text"] == "Verify 'Speed for the mode', depending on the condition:"
    first, *_, odd, default = check["branches"]
    assert first == {
        "if": "If", "condition": 'mode == "fast"', "branch_label": "fast mode",
        "text": "Verify 'Speed': speed >= <limit> rpm", "verb": "Verify", "label": "'Speed':",
        "code": "speed >= <limit> rpm",
    }
    assert odd["source"] == "verify.equal(**checks)" and "text" not in odd
    assert default["if"] == "Otherwise" and "condition" not in default


def test_pages_without_guards_are_unchanged(tmp_path):
    with Recorder() as rec:
        with step("Nothing guarded"):
            pass
    assert ".line.not-run" not in render(run_record([case_record(rec)]), "procedure", "html")


def test_with_pytest_verifier(pytester):
    pytest.importorskip("pytest_verifier", reason="pytest-verifier is not installed")
    pytester.makeini("[pytest]\n")
    pytester.makepyfile(
        test_speed=textwrap.dedent('''
        import pytest
        from stepdoc import step

        @pytest.mark.parametrize("limit", [100])
        @pytest.mark.parametrize("mode", ["fast", "slow", "idle"])
        def test_speed(verify, mode, limit):
            speed = 120 if mode == "fast" else 40
            with step("Check the speed for the mode"):
                verify.guard(
                    [
                        (mode == "fast", "fast mode", lambda: verify.greater_equal(speed, limit, name="Speed")),
                        (mode == "slow", "slow mode", lambda: verify.greater_equal(speed, 50, name="Speed")),
                    ],
                    default=lambda: verify.equal(speed, 40, name="Speed"),
                    name="Speed for the mode",
                )
        ''')
    )
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "--stepdoc-record=run.json", "--stepdoc-procedure=procedure.txt",
        "--stepdoc-report=report.txt",
    )
    result.assert_outcomes(passed=2, failed=1)
    procedure = (pytester.path / "procedure.txt").read_text(encoding="utf-8")
    assert "Variant" not in procedure
    assert [line.strip() for line in procedure.splitlines() if line.strip().startswith("- ")] == [
        "- If mode == \"fast\" (fast mode): Verify 'Speed': speed >= <limit>",
        "- Else if mode == \"slow\" (slow mode): Verify 'Speed': speed >= 50",
        "- Otherwise: Verify 'Speed': speed == 40",
    ]
    report = (pytester.path / "report.txt").read_text(encoding="utf-8")
    assert "- Else if mode == \"slow\" (slow mode): Verify 'Speed': speed >= 50 -> got 40   ✗" in report
