"""The LaTeX procedure and report (OUT-8), and guarded checks (CHK-12).

Most tests build records from pytest-verifier descriptors by hand (the adapter's
``to_check``), so they run without pytest-verifier; the last ones run it, and
compile the output where ``pdflatex`` is installed.
"""

import json
import re
import shutil
import subprocess
import textwrap

import jsonschema
import pytest

from stepdoc import step
from stepdoc.adapters.verify import to_check
from stepdoc.cli import main
from stepdoc.core import Recorder, attach_check, case_record, run_record
from stepdoc.renderers import FORMATS, format_for_path, render
from stepdoc.renderers.latex import escape
from tests.test_adapters import SCHEMA
from tests.test_docstring import EXAMPLE_DOC, GOLDEN, documented_run

pytest_plugins = ["pytester"]

SOURCE = '''\
def test_speed(verify, mode):
    speed = 120 if mode == "fast" else 40
    verify.guard(
        [
            (mode == "fast", "fast mode", lambda: verify.greater_equal(speed, 100, name="Speed")),
            (lambda: mode == "slow", "slow mode", lambda: verify.greater_equal(speed, 50, name="Speed")),
        ],
        default=lambda: verify.equal(speed, 40, name="Speed"),
        name="Speed for the mode",
    )
'''


def leaf(kind, op, actual, expected, line, passed):
    d = {
        "check_type": kind,
        "name": "Speed",
        "description": f"Verify 'Speed' {op} {expected}",
        "actual": actual,
        "phase": "call",
        "location": f"test_speed.py:{line}",
    }
    if passed is not None:
        d["passed"] = passed
    return d


def guard(mode):
    """The descriptor pytest-verifier records for ``SOURCE`` with ``mode``."""
    speed = 120 if mode == "fast" else 40
    fast = leaf("greater_equal", ">=", speed, 100, 5, speed >= 100) if mode == "fast" else None
    slow = leaf("greater_equal", ">=", speed, 50, 6, speed >= 50) if mode == "slow" else None
    default = leaf("equal", "==", speed, 40, 8, speed == 40) if mode == "idle" else None
    matched = {"fast": 0, "slow": 1}.get(mode)
    passed = [c for c in (fast, slow, default) if c][0]["passed"]
    return {
        "check_type": "guard",
        "name": "Speed for the mode",
        "description": "Verify 'Speed for the mode' [guarded]",
        "matched_index": matched,
        "branches": [
            {"condition": mode == "fast", "label": "fast mode", "check": fast},
            {"condition": mode == "slow" if mode != "fast" else None, "label": "slow mode", "check": slow},
        ],
        "default": default,
        "passed": passed,
        "detail": "…",
        "phase": "call",
        "location": "test_speed.py:3",
    }


def speed_run(tmp_path, modes=("fast", "slow", "idle"), source=SOURCE):
    (tmp_path / "test_speed.py").write_text(source)
    cases = []
    for mode in modes:
        d = guard(mode)
        with Recorder() as rec:
            with step("Check the speed for the mode"):
                attach_check(to_check(d, str(tmp_path), frozenset({"mode"})))
        outcome = "passed" if d["passed"] else "failed"
        cases.append(case_record(rec, root=str(tmp_path), test_id=f"test_speed.py::test_speed[{mode}]",
                                 procedure_id="test_speed.py::test_speed", case=mode,
                                 params={"mode": mode}, outcome=outcome))
    return run_record(cases)


def items(tex):
    return [line.strip() for line in tex.splitlines() if line.strip().startswith(r"\item")]


# --------------------------------------------------------------------------- #
# The record
# --------------------------------------------------------------------------- #


def test_guard_records_every_branch(tmp_path):
    record = speed_run(tmp_path)
    jsonschema.validate(record, SCHEMA)
    fast, slow, idle = (t["steps"][0]["entries"][0]["branches"] for t in record["tests"])
    assert [(b["label"], b["condition"], b["default"]) for b in fast] == [
        ("fast mode", 'mode == "fast"', False),
        ("slow mode", 'mode == "slow"', False),  # a lambda condition reads as its body
        (None, None, True),
    ]
    assert [b["taken"] for b in fast] == [True, False, False]
    assert [b["taken"] for b in slow] == [False, True, False]
    assert [b["taken"] for b in idle] == [False, False, True]
    assert [b["holds"] for b in slow] == [False, True, None]
    assert fast[0]["check"]["text"] == "'Speed': speed >= 100" and fast[0]["check"]["passed"] is True
    # A lazy check is built only in its branch: the others keep its source.
    assert fast[1]["check"] is None and fast[1]["source"] == 'verify.greater_equal(speed, 50, name="Speed")'
    assert fast[2]["source"] == 'verify.equal(speed, 40, name="Speed")'


def test_branches_without_source_keep_their_labels(tmp_path):
    # The record says the guard was made on line 3, where this source has no call.
    source = SOURCE.replace("    verify.guard(\n", "    pass\n    verify.guard(\n")
    record = speed_run(tmp_path, ("fast",), source)
    branches = record["tests"][0]["steps"][0]["entries"][0]["branches"]
    assert [b["condition"] for b in branches] == [None, None]
    tex = render(record, "procedure", "tex")
    assert r"\item If fast mode: Verify `Speed{'} \stepdocCode{\char62{}= 100}" in tex
    assert r"\item Else if slow mode: \textit{a check made only when this branch is taken}" in tex


def test_other_checks_have_no_branches(tmp_path):
    with Recorder() as rec:
        with step("Read"):
            attach_check(to_check(leaf("equal", "==", 1, 1, 1, True), str(tmp_path)))
    entry = case_record(rec, root=str(tmp_path))["steps"][0]["entries"][0]
    assert "branches" not in entry


# --------------------------------------------------------------------------- #
# The procedure
# --------------------------------------------------------------------------- #


def test_procedure_lists_every_branch_once(tmp_path):
    tex = render(speed_run(tmp_path), "procedure", "tex")
    assert items(tex) == [
        r"\item[1.] \textbf{Check the speed for the mode}",
        r"\item[1.1.] Verify `Speed for the mode{'}, depending on the condition:",
        r"\item If \stepdocCode{mode == \char34{}fast\char34{}} (fast mode): "
        r"Verify `Speed{'}: \stepdocCode{speed \char62{}= 100}",
        r"\item Else if \stepdocCode{mode == \char34{}slow\char34{}} (slow mode): "
        r"Verify `Speed{'}: \stepdocCode{speed \char62{}= 50}",
        r"\item Otherwise: Verify `Speed{'}: \stepdocCode{speed == 40}",
    ]
    # One procedure for every case, whichever branch each took.
    assert "Procedure for" not in tex and "3 cases" in tex
    assert tex.count(r"\begin{itemize}") == 1


def test_procedure_words_branches_no_case_built(tmp_path):
    tex = render(speed_run(tmp_path, ("fast",)), "procedure", "tex")
    # Only the fast case ran: the other branches' checks are worded from their source.
    assert r"Else if \stepdocCode{mode == \char34{}slow\char34{}} (slow mode): Verify `Speed{'}: \stepdocCode{speed \char62{}= 50}" in tex
    assert r"Otherwise: Verify `Speed{'}: \stepdocCode{speed == 40}" in tex


def test_test_parameters_stay_generic_in_branches(tmp_path):
    source = SOURCE.replace("speed, 100,", "speed, limit,").replace("mode):", "mode, limit):")
    (tmp_path / "test_speed.py").write_text(source)
    d = guard("fast")
    d["branches"][0]["check"]["description"] = "Verify 'Speed' >= 100"
    with Recorder() as rec:
        with step("Check"):
            attach_check(to_check(d, str(tmp_path), frozenset({"mode", "limit"})))
    record = run_record([case_record(rec, root=str(tmp_path), procedure_id="p", params={"limit": 100})])
    assert "speed \\char62{}= \\char60{}limit\\char62{}" in render(record, "procedure", "tex")
    assert "speed \\char62{}= 100}" in render(record, "report", "tex")


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #


def test_report_sets_the_taken_branch_in_bold_and_greys_the_others(tmp_path):
    tex = render(speed_run(tmp_path), "report", "tex")
    fast, slow, idle = re.split(r"\\stepdocTest\{", tex)[1:]
    assert items(fast)[2:] == [
        r"\item \stepdocTaken{If \stepdocCode{mode == \char34{}fast\char34{}} (fast mode): "
        r"Verify `Speed{'}: \stepdocCode{speed \char62{}= 100}} \ensuremath{\rightarrow} \stepdocCode{got 120} \stepdocPass",
        # Not built in this case: the text another case built, its generic form.
        r"\item \stepdocNotRun{Else if \stepdocCode{mode == \char34{}slow\char34{}} (slow mode): "
        r"Verify `Speed{'}: \stepdocCode{speed \char62{}= 50} (not evaluated)}",
        r"\item \stepdocNotRun{Otherwise: Verify `Speed{'}: \stepdocCode{speed == 40} (not evaluated)}",
    ]
    assert r"depending on the condition: \stepdocFail" in slow
    assert r"\stepdocTaken{Else if" in slow and r"\stepdocCode{got 40} \stepdocFail" in slow
    assert "(fast mode): Verify `Speed{'}: \\stepdocCode{speed \\char62{}= 100} (condition false)}" in slow
    assert items(idle)[-1].startswith(r"\item \stepdocTaken{Otherwise: Verify")
    assert "(slow mode): Verify `Speed{'}: \\stepdocCode{speed \\char62{}= 50} (condition false)}" in idle


def test_report_says_why_no_branch_was_taken(tmp_path):
    d = guard("idle")
    d.update(default=None, passed=False, detail="[→ no branch matched: fast mode, slow mode]")
    (tmp_path / "test_speed.py").write_text(SOURCE.replace("        default=lambda: verify.equal(speed, 40, name=\"Speed\"),\n", ""))
    with Recorder() as rec:
        with step("Check"):
            attach_check(to_check(d, str(tmp_path)))
    tex = render(run_record([case_record(rec, root=str(tmp_path), outcome="failed")]), "report", "tex")
    assert r"\stepdocFail \textit{[\ensuremath{\rightarrow} no branch matched: fast mode, slow mode]}" in tex
    assert "Otherwise" not in tex


# --------------------------------------------------------------------------- #
# LaTeX
# --------------------------------------------------------------------------- #


def test_tex_files_pick_the_format():
    assert format_for_path("out/procedure.tex") == "tex" and format_for_path("r.latex") == "tex"
    assert set(FORMATS["tex"]) == {"procedure", "report"}


def test_escape_prints_text_as_it_is():
    assert escape(r"50% of $x_1 & #2 {a} ~^\ 'q' -- <a|b> ∈ ± ✓") == (
        r"50\% of \$x\_1 \& \#2 \{a\} \textasciitilde{}\textasciicircum{}\textbackslash{} "
        r"{'}q{'} {-}{-} \textless{}a\textbar{}b\textgreater{} \ensuremath{\in} \ensuremath{\pm} \stepdocPass{}"
    )


def test_styles_are_provided_not_imposed(tmp_path):
    tex = render(speed_run(tmp_path), "procedure", "tex")
    assert r"\documentclass" not in tex and r"\usepackage" not in tex
    commands = re.findall(r"\\(?:re)?newcommand", tex)
    assert commands == [] and tex.count(r"\providecommand") >= 10


def test_docstring_and_parameters():
    tex = render(documented_run(EXAMPLE_DOC), "procedure", "tex")
    assert r"\textbf{Objective.} Confirm the FIFO accepts its full depth before it reports full." in tex
    assert r"\item[1.] The TX flag sets after exactly 500 writes." in tex
    assert r"\stepdocCode{level} & \stepdocCode{1}, \stepdocCode{2} \\" in tex


def test_deep_steps_stay_within_latex_limits():
    with Recorder() as rec:
        with step("1"), step("2"), step("3"), step("4"), step("5"), step("6"):
            pass
    tex = render(run_record([case_record(rec)]), "report", "tex")
    depth = deepest = 0
    for line in tex.splitlines():
        depth += line.count(r"\begin{enumerate}") - line.count(r"\end{enumerate}")
        deepest = max(deepest, depth)
    assert deepest == 4 and depth == 0
    assert len(items(tex)) == 6


def test_cli_renders_tex(tmp_path, capsys):
    out = tmp_path / "procedure.tex"
    assert main(["render", str(GOLDEN / "record-0.4.json"), "--procedure", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("% Generated by stepdoc.")
    assert main(["render", str(GOLDEN / "record-0.4.json"), "--doc", "report", "--format", "tex"]) == 0
    assert r"\stepdocTitle{Test report}" in capsys.readouterr().out


DOCUMENT = r"""\documentclass{article}
%s
\begin{document}
\input{procedure.tex}
\input{report.tex}
\end{document}
"""


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex is not installed")
@pytest.mark.parametrize("packages", ["", r"\usepackage[T1]{fontenc}\usepackage{xcolor}\usepackage{amssymb}"])
def test_output_compiles(tmp_path, packages):
    records = [json.loads((GOLDEN / "record-0.4.json").read_text()), documented_run(EXAMPLE_DOC), speed_run(tmp_path)]
    for i, record in enumerate(records):
        folder = tmp_path / str(i)
        folder.mkdir()
        (folder / "procedure.tex").write_text(render(record, "procedure", "tex"), encoding="utf-8")
        (folder / "report.tex").write_text(render(record, "report", "tex"), encoding="utf-8")
        (folder / "doc.tex").write_text(DOCUMENT % packages)
        done = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "doc.tex"],
            cwd=folder, capture_output=True, text=True, timeout=120,
        )
        assert done.returncode == 0, done.stdout[-2000:]
        assert "Missing character" not in done.stdout


# --------------------------------------------------------------------------- #
# With the real pytest-verifier
# --------------------------------------------------------------------------- #


def test_with_pytest_verifier(pytester):
    pytest.importorskip("pytest_verifier", reason="pytest-verifier is not installed")
    pytester.makeini("[pytest]\n")
    pytester.makepyfile(
        test_speed=textwrap.dedent('''
        import pytest
        from stepdoc import step

        @pytest.mark.parametrize("mode", ["fast", "slow", "idle"])
        def test_speed(verify, mode):
            speed = 120 if mode == "fast" else 40
            with step("Check the speed for the mode"):
                verify.guard(
                    [
                        (mode == "fast", "fast mode", lambda: verify.greater_equal(speed, 100, name="Speed")),
                        (mode == "slow", "slow mode", verify.greater_equal(speed, 50, name="Speed")),
                    ],
                    default=lambda: verify.equal(speed, 40, name="Speed"),
                    name="Speed for the mode",
                )
        ''')
    )
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "--stepdoc-record=run.json",
        "--stepdoc-procedure=procedure.tex", "--stepdoc-report=report.tex",
    )
    result.assert_outcomes(passed=2, failed=1)
    record = json.loads((pytester.path / "run.json").read_text(encoding="utf-8"))
    jsonschema.validate(record, SCHEMA)
    procedure = (pytester.path / "procedure.tex").read_text(encoding="utf-8")
    assert items(procedure)[2:] == [
        r"\item If \stepdocCode{mode == \char34{}fast\char34{}} (fast mode): Verify `Speed{'}: \stepdocCode{speed \char62{}= 100}",
        r"\item Else if \stepdocCode{mode == \char34{}slow\char34{}} (slow mode): Verify `Speed{'}: \stepdocCode{speed \char62{}= 50}",
        r"\item Otherwise: Verify `Speed{'}: \stepdocCode{speed == 40}",
    ]
    report = (pytester.path / "report.tex").read_text(encoding="utf-8")
    assert report.count(r"\stepdocTaken{") == 3
    assert r"\stepdocCode{got 40} \stepdocFail" in report
