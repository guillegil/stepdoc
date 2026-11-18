"""Operand names with the real pytest-verifier (``actual_name=``, ``expected_name=``).

These tests run pytest-verifier itself, so they are skipped where it is not
installed (stepdoc's own environment) or is a version without operand names.
tests/test_verify_text.py covers the same wording with a fake plugin.
"""

import inspect
import json
import re
import sys

import jsonschema
import pytest

from stepdoc.renderers import render
from tests.test_adapters import SCHEMA

pytest_verifier = pytest.importorskip("pytest_verifier", reason="pytest-verifier is not installed")
_equal = getattr(getattr(pytest_verifier, "Verify", None), "equal", None)
if _equal is None or "actual_name" not in inspect.signature(_equal).parameters:
    pytest.skip("this pytest-verifier has no actual_name=", allow_module_level=True)

pytest_plugins = ["pytester"]

# A register map whose field reads are recorded by the bridge in the conftest.
DEVICE = '''
class Reg:
    def __init__(self, dev, path, value):
        self._dev, self._path, self._value = dev, path, value

    @property
    def F(self):
        for hook in self._dev.on_read:
            hook(f"{self._path}.F", self._value)
        return self._value


class Block:
    def __init__(self, dev):
        for ch in range(4):
            setattr(self, f"reg{ch}", Reg(dev, f"tb.dev.map.block.reg{ch}", 243))


class Map:
    def __init__(self, dev):
        self.block = Block(dev)


class Dev:
    def __init__(self):
        self.on_read = []
        self.map = Map(self)


class Testbench:
    def __init__(self):
        self.dev = Dev()
'''

CONFTEST = '''
import pytest

import stepdoc
from regmap import Testbench


@pytest.fixture
def tb():
    t = Testbench()
    t.dev.on_read.append(lambda path, value: stepdoc.record_action(path, "read", value))
    return t
'''


@pytest.fixture
def project(pytester):
    pytester.makepyfile(regmap=DEVICE)
    pytester.makeconftest(CONFTEST)
    pytester.makeini("[pytest]\nstepdoc_skip_modules = regmap\nenable_assertion_pass_hook = true\n")
    return pytester


def run(pytester, *args):
    out = ("--stepdoc-record=run.json", "--stepdoc-procedure=procedure.txt", "--stepdoc-report=report.txt")
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *out, *args)


def numbered(pytester, name):
    text = (pytester.path / name).read_text(encoding="utf-8")
    return [line.rstrip() for line in text.splitlines() if re.match(r"\s*(\d|- )", line)]


def record_of(pytester):
    record = json.loads((pytester.path / "run.json").read_text(encoding="utf-8"))
    jsonschema.validate(record, SCHEMA)
    return record


def test_names_word_the_check_in_both_documents(project):
    project.makepyfile(
        test_readback='''
        import pytest
        from stepdoc import step


        @pytest.mark.parametrize("channel", [3])
        def test_readback(verify, tb, channel):
            wdata = 243
            with step("Read the word back"):
                block_reg_f = getattr(tb.dev.map.block, f"reg{channel}").F
                verify.equal(block_reg_f, wdata,
                             actual_name=f"tb.dev.map.block.reg{channel}.F",
                             expected_name="wdata")
                verify.equal(block_reg_f, wdata, name="Stored word",
                             actual_name=f"tb.dev.map.block.reg{channel}.F", expected_name="wdata")
                verify.equal(block_reg_f, 243, actual_name=f"tb.dev.map.block.reg{channel}.F")
            with step("Supply"):
                vout = 3.31
                nominal = 3.3
                verify.approx(vout, nominal, abs_tol=0.05, units="V",
                              actual_name="psu.vout", expected_name="nominal")
        '''
    )
    run(project).assert_outcomes(passed=1)
    assert numbered(project, "procedure.txt") == [
        "1. Read the word back",
        "   1.1. Read tb.dev.map.block.reg<channel>.F -> <block_reg_f>",
        "   1.2. Verify tb.dev.map.block.reg<channel>.F == <wdata>",
        "   1.3. Verify 'Stored word': tb.dev.map.block.reg<channel>.F == <wdata>",
        "   1.4. Verify tb.dev.map.block.reg<channel>.F == 243",
        "2. Supply",
        "   2.1. Verify psu.vout == <nominal> V ± 0.05V",
    ]
    # Step lines carry their duration: compare the entries.
    assert [line for line in numbered(project, "report.txt") if line.startswith(" ")] == [
        "   1.1. Read tb.dev.map.block.reg3.F -> 243",
        "   1.2. Verify tb.dev.map.block.reg3.F == wdata (243) -> got 243   ✓",
        "   1.3. Verify 'Stored word': tb.dev.map.block.reg3.F == wdata (243) -> got 243   ✓",
        "   1.4. Verify tb.dev.map.block.reg3.F == 243 -> got 243   ✓",
        "   2.1. Verify psu.vout == nominal (3.3V) ± 0.05V -> got 3.31   ✓",
    ]
    record = record_of(project)
    first, named = record["tests"][0]["steps"][0]["entries"][1:3]
    assert first["text"] == "tb.dev.map.block.reg3.F == wdata (243)"
    assert first["symbolic_text"] == "tb.dev.map.block.reg<channel>.F == <wdata>"
    assert first["operands"] == {
        "actual": {"concrete": "tb.dev.map.block.reg3.F", "symbolic": "tb.dev.map.block.reg<channel>.F"},
        "expected": {"concrete": "wdata", "symbolic": "wdata"},
    }
    assert "label" not in first and named["label"] == "Stored word"
    html = render(record, "procedure", "html")
    code = '<code class="expr">tb.<wbr>dev.<wbr>map.<wbr>block.<wbr>reg&lt;channel&gt;.<wbr>F == &lt;wdata&gt;</code>'
    assert f'<span class="label">&#x27;Stored word&#x27;:</span> {code}' in html
    assert "reg3" not in html and "<channel>" not in html
    assert "Verify 'Stored word': `tb.dev.map.block.reg<channel>.F == <wdata>`" in render(record, "procedure", "md")


def test_every_check_type_takes_its_names(project):
    project.makepyfile(
        test_kinds='''
        import pytest
        from stepdoc import step


        @pytest.mark.parametrize("ch", [2])
        def test_kinds(verify, ch):
            low = 5
            with step("Kinds"):
                verify.not_equal(1, low, actual_name=f"a{ch}", expected_name="low")
                verify.greater(9, low, units="mA", actual_name=f"a{ch}", expected_name=f"limit[{ch}]")
                verify.greater_equal(9, low, actual_name=f"a{ch}", expected_name="low")
                verify.less(1, low, actual_name=f"a{ch}", expected_name="low")
                verify.less_equal(1, low, actual_name=f"a{ch}", expected_name="low")
                verify.length([1, 2], 2, actual_name=f"rx{ch}.frames", expected_name="n_frames")
                verify.between(5, 1, 9, units="A", actual_name=f"psu{ch}.i")
                verify.is_true(True, actual_name=f"dev{ch}.ready")
                verify.is_false(False, actual_name=f"dev{ch}.busy")
                verify.is_none(None, actual_name=f"dev{ch}.error")
                verify.is_not_none(1, actual_name=f"dev{ch}.id")
                verify.contains("abc", "b", actual_name=f"log[{ch}]")
                verify.not_contains("abc", "z", actual_name=f"log[{ch}]")
                verify.matches("v1", r"^v\\d", actual_name=f"fw{ch}.version")
                verify.is_instance(3, int, actual_name=f"cfg{ch}.n")
                verify.approx(50.0, 50, rel_tol=0.01, units="%", actual_name=f"duty{ch}", expected_name="target")
                verify.less(5, 30, actual_name="it's `odd` == (x) < y", expected_name="a (b) ± c == d")
        '''
    )
    run(project).assert_outcomes(passed=1)
    assert numbered(project, "procedure.txt")[1:] == [
        "   1.1. Verify a<ch> != <low>",
        "   1.2. Verify a<ch> > limit[<ch>] mA",
        "   1.3. Verify a<ch> >= <low>",
        "   1.4. Verify a<ch> < <low>",
        "   1.5. Verify a<ch> <= <low>",
        "   1.6. Verify rx<ch>.frames has length <n_frames>",
        "   1.7. Verify psu<ch>.i ∈ [1A, 9A]",
        "   1.8. Verify dev<ch>.ready is True",
        "   1.9. Verify dev<ch>.busy is False",
        "   1.10. Verify dev<ch>.error is None",
        "   1.11. Verify dev<ch>.id is not None",
        "   1.12. Verify log[<ch>] contains 'b'",
        "   1.13. Verify log[<ch>] does not contain 'z'",
        "   1.14. Verify fw<ch>.version matches /^v\\d/",
        "   1.15. Verify cfg<ch>.n is instance of int",
        "   1.16. Verify duty<ch> == <target> % ± 1% (rel)",
        "   1.17. Verify it's `odd` == (x) < y < <a (b) ± c == d>",
    ]
    report = numbered(project, "report.txt")[1:]
    assert report[0] == "   1.1. Verify a2 != low (5) -> got 1   ✓"
    assert report[1] == "   1.2. Verify a2 > limit[2] (5mA) -> got 9   ✓"
    assert report[5] == "   1.6. Verify rx2.frames has length n_frames (2) -> got [1, 2]   ✓"
    assert report[11] == "   1.12. Verify log[2] contains 'b'   ✓"
    assert report[15] == "   1.16. Verify duty2 == target (50%) ± 1% (rel) -> got 50.0   ✓"
    assert report[16] == "   1.17. Verify it's `odd` == (x) < y < a (b) ± c == d (30) -> got 5   ✓"
    record = record_of(project)
    html = render(record, "procedure", "html")
    assert '<code class="expr">it&#x27;s `odd` == (x) &lt; y &lt; &lt;a (b) ± c == d&gt;</code>' in html
    assert "- 1.17. Verify `` it's `odd` == (x) < y < <a (b) ± c == d> ``" in render(record, "procedure", "md")


def test_a_loop_over_channels_reads_the_same_every_pass(project):
    project.makepyfile(
        test_loop='''
        import pytest
        from stepdoc import step


        @pytest.mark.parametrize("wdata", [243])
        def test_all_channels(verify, tb, wdata):
            with step("Read every channel back"):
                for channel in range(3):
                    verify.equal(getattr(tb.dev.map.block, f"reg{channel}").F, wdata,
                                 actual_name=f"tb.dev.map.block.reg{channel}.F", expected_name="wdata")
        '''
    )
    run(project).assert_outcomes(passed=1)
    assert numbered(project, "procedure.txt")[1:] == [
        f"   1.{i}. Verify tb.dev.map.block.reg<channel>.F == <wdata>" for i in (1, 2, 3)
    ]
    assert numbered(project, "report.txt")[1:] == [
        f"   1.{i + 1}. Verify tb.dev.map.block.reg{i}.F == wdata (243) -> got 243   ✓" for i in range(3)
    ]
    # Each pass's register read went into its check, as without the names.
    entries = record_of(project)["tests"][0]["steps"][0]["entries"]
    assert [[r["target"]["concrete"] for r in e["reads"]] for e in entries] == [
        [f"tb.dev.map.block.reg{i}.F"] for i in range(3)
    ]


def test_notes_failures_require_and_two_checks_on_a_line(project):
    project.makepyfile(
        test_more='''
        import pytest
        from stepdoc import step


        @pytest.mark.parametrize("channel", [1])
        def test_more(verify, tb, channel):
            wdata = 240
            with step("Notes"):
                f = getattr(tb.dev.map.block, f"reg{channel}").F
                # the word written before
                verify.equal(f, 243, actual_name=f"block.reg{channel}.F")
                verify.equal(f, 243, actual_name=f"block.reg{channel}.F", note="given")
            with step("One line"):
                verify.equal(f, 243, actual_name="first.F"); verify.equal(f, 243, actual_name="second.F")
            with step("Fails"):
                verify.equal(f, wdata, actual_name=f"block.reg{channel}.F", expected_name="wdata")
                verify.require.equal(f, wdata, name="Must match",
                                     actual_name=f"block.reg{channel}.F", expected_name="wdata")
                verify.is_true(False, name="never reached")
        '''
    )
    run(project).assert_outcomes(failed=1)
    assert numbered(project, "procedure.txt") == [
        "1. Notes",
        "   1.1. Read tb.dev.map.block.reg<channel>.F -> <f>",
        "   1.2. Verify block.reg<channel>.F == 243 (the word written before)",
        "   1.3. Verify block.reg<channel>.F == 243 (given)",
        "2. One line",
        "   2.1. Verify first.F == 243",
        "   2.2. Verify second.F == 243",
        "3. Fails",
        # verify.require stopped the test inside the step: its error is the step's own check.
        "   3.1. Verify 2 of 6 checks failed, stopped at [5]: Must match — expected wdata (240), got 243 (+1 more)",
        "   3.2. Verify block.reg<channel>.F == <wdata>",
        "   3.3. Verify 'Must match': block.reg<channel>.F == <wdata>",
    ]
    report = numbered(project, "report.txt")
    assert "   1.2. Verify block.reg1.F == 243 (the word written before) -> got 243   ✓" in report
    assert "   2.2. Verify second.F == 243 -> got 243   ✓" in report
    assert "   3.2. Verify block.reg1.F == wdata (240) -> got 243   ✗" in report
    assert "   3.3. Verify 'Must match': block.reg1.F == wdata (240) -> got 243   ✗" in report
    assert not [line for line in report if "never reached" in line]
    failed = record_of(project)["tests"][0]["steps"][2]["entries"][1]
    assert failed["detail"] == "expected wdata (240), got 243"


def test_calls_on_one_line_and_names_the_description_shows_another_way(project):
    project.makepyfile(
        test_edges='''
        import pytest
        from stepdoc import step


        @pytest.mark.parametrize("channel", [3])
        def test_edges(verify, tb, channel):
            w = 243
            with step("One line"):
                verify.equal(tb.dev.map.block.reg1.F, w, actual_name="R1"); verify.equal(tb.dev.map.block.reg2.F, w, actual_name=f" R{channel - 1}.F ")
            with step("Names that read alike"):
                verify.equal(tb.dev.map.block.reg1.F, w, actual_name=f"R{channel}"); verify.equal(tb.dev.map.block.reg2.F, w, actual_name=f"R{channel - 1}")
            with step("Expected names"):
                verify.equal(w, 243, actual_name="act", expected_name=f"limit\\t{channel}")
                verify.equal(w, 243, actual_name="act", expected_name=f"table[{channel}]." + "long_field_name." * 16)
                verify.approx(w, 243, abs_tol=1, rel_tol=0.01, units="mV", actual_name="act",
                              expected_name=f"table[{channel}]." + "long_field_name." * 16)
                verify.equal(w, 243, actual_name="act", expected_name="a < b > c")
        '''
    )
    run(project).assert_outcomes(passed=1)
    long = "table[3]." + "long_field_name." * 16
    # Before Python 3.11 pytest-verifier cannot tell which call on a line is
    # running, so it gives no generic form there: the names are as given.
    generic = sys.version_info >= (3, 11)
    assert numbered(project, "procedure.txt") == [
        "1. One line",
        "   1.1. Verify R1 == <w>",
        "   1.2. Verify R<channel - 1>.F == <w>" if generic else "   1.2. Verify R2.F == <w>",
        "2. Names that read alike",
        # Either call may have made each check: reads on their own lines, the value as it was.
        "   2.1. Read tb.dev.map.block.reg1.F",
        "   2.2. Read tb.dev.map.block.reg2.F",
        "   2.3. Verify R<channel> == 243" if generic else "   2.3. Verify R3 == 243",
        "   2.4. Verify R<channel - 1> == 243" if generic else "   2.4. Verify R2 == 243",
        "3. Expected names",
        "   3.1. Verify act == limit <channel>",
        f"   3.2. Verify act == <{long}>",
        f"   3.3. Verify act == <{long}> mV ± 1mV (abs) ± 1% (rel)",
        "   3.4. Verify act == <a < b > c>",
    ]
    report = numbered(project, "report.txt")
    assert "   1.2. Verify R2.F == 243 -> got 243   ✓" in report
    assert "   3.4. Verify act == a < b > c (243) -> got 243   ✓" in report
    # Each check took the read of its own call.
    entries = record_of(project)["tests"][0]["steps"][0]["entries"]
    assert [[r["target"]["concrete"] for r in e["reads"]] for e in entries] == [
        ["tb.dev.map.block.reg1.F"], ["tb.dev.map.block.reg2.F"],
    ]


def test_composite_children_and_dry_run(project):
    project.makepyfile(
        test_children='''
        import pytest
        from stepdoc import step


        def test_children(verify):
            wdata = 243
            with step("Composites"):
                verify.all_satisfy(
                    [0, 1],
                    lambda ch: verify.equal(243, wdata, actual_name=f"reg{ch}.F", expected_name="wdata"),
                    name="Every register",
                )
        '''
    )
    run(project).assert_outcomes(passed=1)
    procedure = numbered(project, "procedure.txt")
    report = numbered(project, "report.txt")
    assert [line.strip() for line in procedure if line.strip().startswith("- ")] == [
        "- Verify reg<ch>.F == <wdata>", "- Verify reg<ch>.F == <wdata>",
    ]
    assert [line.strip() for line in report if line.strip().startswith("- ")] == [
        "- Verify reg0.F == wdata (243) -> got 243   ✓",
        "- Verify reg1.F == wdata (243) -> got 243   ✓",
    ]
    record_of(project)
    # Dry run: the same procedure, nothing judged in the report.
    run(project, "--stepdoc-dry-run")
    assert numbered(project, "procedure.txt") == procedure
    dry = numbered(project, "report.txt")
    assert "      - Verify reg0.F == wdata (243) -> got 243   ·" in dry
    record_of(project)


@pytest.mark.skipif(sys.version_info < (3, 14), reason="template strings need Python 3.14")
def test_template_strings_carry_both_forms(project):
    project.makepyfile(
        test_tstring='''
        import pytest
        from stepdoc import step


        def spelled(channel):
            return t"tb.dev.map.block.reg{channel}.F"


        @pytest.mark.parametrize("channel", [3])
        def test_t(verify, channel):
            name = spelled(channel)
            with step("Template"):
                verify.equal(243, 243, actual_name=name, expected_name=t"words[{channel}]")
        '''
    )
    run(project).assert_outcomes(passed=1)
    assert numbered(project, "procedure.txt")[1:] == [
        "   1.1. Verify tb.dev.map.block.reg<channel>.F == words[<channel>]",
    ]
    assert numbered(project, "report.txt")[1:] == [
        "   1.1. Verify tb.dev.map.block.reg3.F == words[3] (243) -> got 243   ✓",
    ]
