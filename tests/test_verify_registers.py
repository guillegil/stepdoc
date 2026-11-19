"""Register checks: values in hex, the bits that differ, ``verify.bits``.

A pytest-verifier check that shows its values in a format (``radix=``,
``width=``, ``bits``, a value with ``__verify_format__``) carries its own text of
the value it got (``rendered``) and of the bits that differ (``bit_diff``). The
report shows them, ``-> got 0x00F0, bits [1:0] differ``; the procedure shows
neither. The first half feeds records shaped like pytest-verifier's to the
adapter, so it runs everywhere; the second half runs the real plugin and is
skipped where pytest-verifier is not installed or has no ``bits``.
"""

import json
from pathlib import Path

import jsonschema
import pytest

from stepdoc import Check, Recorder, repeat, step
from stepdoc.adapters.verify import to_check
from stepdoc.core.record import case_record, dumps, load, run_record
from stepdoc.renderers import render
from stepdoc.renderers.common import procedures
from tests.helpers import procedure, report

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "src/stepdoc/schema/run-record.schema.json").read_text()
)

# Records as pytest-verifier 0.11 publishes them (copied from a run).
EQUAL = {
    "check_type": "equal", "name": "R", "description": "Verify 'R' == 0x00F3", "actual": 240,
    "expected": 243, "units": None, "radix": "hex", "width": 16,
    "rendered": {"actual": "0x00F0", "expected": "0x00F3"},
    "bit_diff": {"bits": [[1, 0]], "text": "bits [1:0] differ"}, "passed": False,
    "detail": "expected 0x00F3, got 0x00F0; bits [1:0] differ", "phase": "call",
}
FIELDS = [
    {"name": "EN", "msb": 0, "lsb": 0, "actual": "0b0", "expected": "0b1"},
    {"name": "MODE", "msb": 3, "lsb": 1, "actual": "0b000", "expected": "0b001"},
]
BITS = {
    "check_type": "bits", "name": "CTRL", "description": "Verify 'CTRL' & 0x00FF == 0x00F3",
    "actual": 4848, "expected": 44019, "mask": 255, "radix": "hex", "width": 16,
    "fields": {"EN": [0, 0], "MODE": [3, 1]},
    "rendered": {"actual": "0x12F0", "expected": "0xABF3", "mask": "0x00FF"},
    "bit_diff": {"bits": [[1, 0]], "text": "bits [1:0] differ", "fields": FIELDS}, "passed": False,
    "detail": "expected 0x00F3, got 0x12F0 (mask 0x00FF); bits [1:0] differ: EN [0] is 0b0, "
    "expected 0b1; MODE [3:1] is 0b000, expected 0b001",
    "phase": "call",
}
BITS_PASSED = {
    "check_type": "bits", "name": "CTRL", "description": "Verify 'CTRL' & 0x00FF == 0x00F3",
    "actual": 4851, "expected": 44019, "mask": 255, "radix": "hex", "width": 16,
    "rendered": {"actual": "0x12F3", "expected": "0xABF3", "mask": "0x00FF"}, "passed": True,
    "detail": "0x12F3 & 0x00FF == 0x00F3", "phase": "call",
}
WIDE = {
    "check_type": "equal", "name": "W", "description": "Verify 'W' == 0xFFFF_FFFF_FFFF_FFFE",
    "actual": 18446744073709551615, "expected": 18446744073709551614, "units": None, "radix": "hex",
    "width": 64, "rendered": {"actual": "0xFFFF_FFFF_FFFF_FFFF", "expected": "0xFFFF_FFFF_FFFF_FFFE"},
    "bit_diff": {"bits": [[0, 0]], "text": "bit 0 differs"}, "passed": False,
    "detail": "expected 0xFFFF_FFFF_FFFF_FFFE, got 0xFFFF_FFFF_FFFF_FFFF; bit 0 differs",
}
DIFFERENCE = "bits [1:0] differ: EN [0] is 0b0, expected 0b1; MODE [3:1] is 0b000, expected 0b001"


def _recorded(*checks):
    with Recorder() as rec:
        pass
    rec.unscoped.extend(checks)
    return rec


def _record(*checks):
    record = run_record([case_record(_recorded(*checks))])
    jsonschema.validate(record, SCHEMA)
    return record


def _shown(rec):
    """The report's lines of checks recorded outside a step."""
    return [line.strip() for line in report(rec).splitlines()[1:]]


def _body(html):
    return html.split("</style>")[-1]


# --------------------------------------------------------------------------- #
# Records shaped like pytest-verifier's (no plugin needed)
# --------------------------------------------------------------------------- #


def test_the_report_shows_the_value_as_the_check_does_and_what_differs(tmp_path):
    check = to_check(EQUAL, str(tmp_path))
    assert (check.actual, check.actual_text, check.difference) == (240, "0x00F0", "bits [1:0] differ")
    record = _record(check)
    entry = record["tests"][0]["unscoped"][0]
    assert entry["actual"] == 240 and entry["actual_text"] == "0x00F0"
    assert entry["difference"] == "bits [1:0] differ"
    assert entry["detail"] == "expected 0x00F3, got 0x00F0; bits [1:0] differ"
    assert "- Verify 'R' == 0x00F3 -> got 0x00F0, bits [1:0] differ   ✗" in render(record, "report", "text")
    assert "- Verify 'R' `== 0x00F3` → `got 0x00F0, bits [1:0] differ` ✗" in render(record, "report", "md")
    html = render(record, "report", "html")
    assert '<span class="result">→ <code>got 0x00F0, bits [1:0] differ</code></span>' in html
    # The procedure says what is verified, never what this run got.
    assert render(record, "procedure", "text").splitlines()[-1] == "   - Verify 'R' == 0x00F3"
    for fmt in ("text", "md", "html"):
        text = _body(render(record, "procedure", fmt))
        assert "0x00F0" not in text and "differ" not in text and "240" not in text


def test_the_fields_that_differ_follow_the_bits(tmp_path):
    check = to_check(BITS, str(tmp_path))
    assert check.text == "'CTRL' & 0x00FF == 0x00F3" and check.label == "CTRL"
    assert check.difference == DIFFERENCE
    record = _record(check)
    assert f"- Verify 'CTRL' & 0x00FF == 0x00F3 -> got 0x12F0, {DIFFERENCE}   ✗" in render(
        record, "report", "text"
    )
    assert f"- Verify 'CTRL' `& 0x00FF == 0x00F3` → `got 0x12F0, {DIFFERENCE}` ✗" in render(record, "report", "md")
    html = render(record, "report", "html")
    assert f"<code>got 0x12F0, {DIFFERENCE}</code>" in html
    assert '<span class="label">&#x27;CTRL&#x27;</span> <code class="expr">&amp; 0x00FF == 0x00F3</code>' in html


def test_a_passed_check_shows_its_value_and_no_difference(tmp_path):
    check = to_check(BITS_PASSED, str(tmp_path))
    assert (check.actual_text, check.difference) == ("0x12F3", None)
    assert _shown(_recorded(check)) == ["- Verify 'CTRL' & 0x00FF == 0x00F3 -> got 0x12F3   ✓"]
    # Whatever an adapter stores, only a failed check says what differs.
    for passed, mark in ((True, "✓"), (None, "·")):
        odd = Check("x == 1", passed, actual=3, actual_text="0x3", difference="bits [1:0] differ")
        assert _shown(_recorded(odd)) == [f"- Verify x == 1 -> got 0x3   {mark}"]
    # A difference alone is the result.
    alone = Check("x matches y", False, difference="line 3 differs")
    assert _shown(_recorded(alone)) == ["- Verify x matches y -> line 3 differs   ✗"]


def test_values_wider_than_a_float_holds_are_exact(tmp_path):
    record = _record(to_check(WIDE, str(tmp_path)))
    assert (
        "- Verify 'W' == 0xFFFF_FFFF_FFFF_FFFE -> got 0xFFFF_FFFF_FFFF_FFFF, bit 0 differs   ✗"
        in render(record, "report", "text")
    )
    assert record["tests"][0]["unscoped"][0]["actual"] == 18446744073709551615


def test_ordering_checks_show_the_value_in_their_format(tmp_path):
    less = {"check_type": "less", "name": "ADDR", "description": "Verify 'ADDR' < 0x100", "actual": 496,
            "threshold": 256, "radix": "hex", "rendered": {"actual": "0x1F0", "threshold": "0x100"},
            "passed": False, "detail": "expected < 0x100, got 0x1F0"}
    between = {"check_type": "between", "name": "N", "description": "Verify 'N' ∈ [0b1000, 0b1100]",
               "actual": 5, "low": 8, "high": 12, "radix": "bin", "width": 4, "passed": False,
               "rendered": {"actual": "0b0101", "low": "0b1000", "high": "0b1100"}}
    assert _shown(_recorded(to_check(less, str(tmp_path)), to_check(between, str(tmp_path)))) == [
        "- Verify 'ADDR' < 0x100 -> got 0x1F0   ✗",
        "- Verify 'N' ∈ [0b1000, 0b1100] -> got 0b0101   ✗",
    ]


def test_many_fields_are_cut_like_pytest_verifiers_detail(tmp_path):
    fields = [{"name": f"F{i}", "msb": i, "lsb": i, "actual": "0b0", "expected": "0b1"} for i in range(8)]
    d = {**EQUAL, "bit_diff": {"bits": [[7, 0]], "text": "bits [7:0] differ", "fields": fields}}
    difference = to_check(d, str(tmp_path)).difference
    assert difference.startswith("bits [7:0] differ: F0 [0] is 0b0, expected 0b1; F1 [1] is")
    assert difference.endswith("; F5 [5] is 0b0, expected 0b1; and 2 more")
    d["bit_diff"]["more_fields"] = 10
    assert to_check(d, str(tmp_path)).difference.endswith("; and 12 more")


@pytest.mark.parametrize(
    "diff, expected",
    [
        (None, None),
        ("bits [1:0] differ", None),
        ({}, None),
        ({"text": ""}, None),
        ({"text": 3}, None),
        ({"text": "bit 4 differs", "fields": "EN"}, "bit 4 differs"),
        ({"text": "bit 4 differs", "fields": [{"name": "EN"}]}, "bit 4 differs"),
        ({"text": "bit 4 differs", "fields": ["EN"]}, "bit 4 differs"),
        ({"text": "bit 4\ndiffers", "fields": []}, "bit 4 differs"),
    ],
)
def test_a_difference_of_another_shape_never_breaks_the_document(tmp_path, diff, expected):
    check = to_check({**EQUAL, "bit_diff": diff}, str(tmp_path))
    assert check.difference == expected
    _record(check)
    for rendered in (None, "0x00F0", {}, {"actual": 240}, {"actual": " "}):
        assert to_check({**EQUAL, "rendered": rendered}, str(tmp_path)).actual_text is None


def test_records_without_a_format_read_as_before(tmp_path):
    """A record of an older pytest-verifier, or of a check without a format: the
    report shows stepdoc's own rendering and the run record has no new key."""
    old = {"check_type": "equal", "name": "R", "description": "Verify 'R' == 243", "actual": 240,
           "expected": 243, "passed": False, "detail": "expected 243, got 240"}
    check = to_check(old, str(tmp_path))
    assert (check.actual_text, check.difference) == (None, None)
    record = _record(check)
    entry = record["tests"][0]["unscoped"][0]
    assert "actual_text" not in entry and "difference" not in entry
    assert "- Verify 'R' == 243 -> got 240   ✗" in render(record, "report", "text")
    assert "- Verify 'R' `== 243` → `got 240` ✗" in render(record, "report", "md")
    # An unknown check type whose description has the bits shape, without rendered text.
    bits = {"check_type": "bits", "name": "R", "description": "Verify 'R' & 0xFF == 0xF3", "actual": 240,
            "passed": False}
    assert _shown(_recorded(to_check(bits, str(tmp_path)))) == ["- Verify 'R' & 0xFF == 0xF3 -> got 240   ✗"]


SOURCE = '''\
MASK = 0x00FF


def test_ctrl(verify, reg, wdata):
    verify.bits(reg.CTRL, wdata, mask=MASK, width=16)
    verify.bits(reg.CTRL, wdata, mask=0x00FF, width=16)
    verify.bits(reg.CTRL, 0xABF3, mask=MASK, width=16)
    verify.bits(reg.CTRL, 0xABF3, mask=0x00FF, width=16)
    verify.bits(reg.CTRL, wdata, width=16)
    verify.bits(reg.CTRL, 0x00F3, width=16)
    verify.bits(reg.CTRL, wdata, mask=MASK, width=16, name="Control")
    verify.bits(reg.CTRL, expected=wdata, mask=MASK, expected_name="wdata", actual_name="x")
    verify.bits(0x12F0, wdata, mask=MASK, name="Control")
'''


def _bits(line, **over):
    d = {**BITS, "name": "reg.CTRL", "auto_name": True, "location": f"test_ctrl.py:{line}",
         "description": "Verify 'reg.CTRL' & 0x00FF == 0x00F3"}
    d.update(over)
    return d


NO_MASK = {"description": "Verify 'reg.CTRL' == 0x00F3", "mask": None,
           "rendered": {"actual": "0x12F0", "expected": "0x00F3"}}


@pytest.mark.parametrize(
    "d, text, symbolic",
    [
        # Expected value and mask given by name: neither value is in the procedure.
        (_bits(5), "reg.CTRL & 0x00FF == 0x00F3", "reg.CTRL & <MASK> == <wdata>"),
        (_bits(6), "reg.CTRL & 0x00FF == 0x00F3", "reg.CTRL & 0x00FF == <wdata>"),
        # A literal beside a named mask is shown whole: under the mask it is a run value.
        (_bits(7), "reg.CTRL & 0x00FF == 0x00F3", "reg.CTRL & <MASK> == 0xABF3"),
        (_bits(8), "reg.CTRL & 0x00FF == 0x00F3", None),
        (_bits(9, **NO_MASK), "reg.CTRL == 0x00F3", "reg.CTRL == <wdata>"),
        (_bits(10, **NO_MASK), "reg.CTRL == 0x00F3", None),
        # A check with a name of its own keeps it, as the other value checks do.
        (
            _bits(11, name="Control", auto_name=False, description="Verify 'Control' & 0x00FF == 0x00F3"),
            "'Control': reg.CTRL & 0x00FF == 0x00F3",
            "'Control': reg.CTRL & <MASK> == <wdata>",
        ),
        # Operand names word both sides.
        (
            _bits(12, name="reg3.CTRL", actual_name="reg3.CTRL", actual_name_symbolic="reg<ch>.CTRL",
                  expected_name="wdata", description="Verify 'reg3.CTRL' & 0x00FF == wdata (0x00F3)"),
            "reg3.CTRL & 0x00FF == wdata (0x00F3)",
            "reg<ch>.CTRL & <MASK> == <wdata>",
        ),
        # A literal checked value keeps the quoted name.
        (
            _bits(13, name="Control", auto_name=False, description="Verify 'Control' & 0x00FF == 0x00F3"),
            "'Control' & 0x00FF == 0x00F3",
            "'Control' & <MASK> == <wdata>",
        ),
        # No source: the description, as for every check.
        (_bits(5, location=None), "'reg.CTRL' & 0x00FF == 0x00F3", None),
        # An operand name without the source still words the check.
        (
            _bits(5, location=None, name="reg3.CTRL", actual_name="reg3.CTRL", expected_name="wdata",
                  description="Verify 'reg3.CTRL' & 0x00FF == wdata (0x00F3)"),
            "reg3.CTRL & 0x00FF == wdata (0x00F3)",
            "reg3.CTRL & 0x00FF == <wdata>",
        ),
    ],
)
def test_bits_wording(tmp_path, d, text, symbolic):
    (tmp_path / "test_ctrl.py").write_text(SOURCE)
    check = to_check(d, str(tmp_path))
    assert (check.text, check.symbolic_text) == (text, symbolic)
    record = _record(check)
    assert f"Verify {text} -> got 0x12F0, {DIFFERENCE}   ✗" in render(record, "report", "text")
    if symbolic is not None and "0x" not in symbolic:
        for fmt in ("text", "md", "html"):  # no value of this run in the procedure
            shown = _body(render(record, "procedure", fmt))
            assert "0x" not in shown and "differ" not in shown


def test_bits_operand_names_and_label_in_the_record(tmp_path):
    (tmp_path / "test_ctrl.py").write_text(SOURCE)
    d = _bits(12, name="Control", auto_name=False, actual_name="reg3.CTRL", actual_name_symbolic="reg<ch>.CTRL",
              expected_name="wdata", description="Verify 'Control' & 0x00FF == wdata (0x00F3)")
    check = to_check(d, str(tmp_path))
    assert check.text == "'Control': reg3.CTRL & 0x00FF == wdata (0x00F3)"
    assert check.symbolic_text == "'Control': reg<ch>.CTRL & <MASK> == <wdata>"
    assert check.label == "Control"
    assert check.operands == {
        "actual": {"concrete": "reg3.CTRL", "symbolic": "reg<ch>.CTRL"},
        "expected": {"concrete": "wdata", "symbolic": "wdata"},
    }
    html = render(_record(check), "procedure", "html")
    assert '<span class="label">&#x27;Control&#x27;:</span> <code class="expr">reg&lt;ch&gt;.<wbr>CTRL &amp; ' in html


def test_cases_with_different_values_share_one_procedure(tmp_path):
    (tmp_path / "test_ctrl.py").write_text(SOURCE)
    cases = []
    for i, (mask, expected, got) in enumerate((("0x00FF", "0x00F3", "0x12F0"), ("0xFF00", "0x1200", "0x12F3"))):
        d = _bits(5, description=f"Verify 'reg.CTRL' & {mask} == {expected}",
                  rendered={"actual": got, "expected": "0xABF3", "mask": mask})
        rec = _recorded(to_check(d, str(tmp_path)), to_check({**EQUAL, "rendered": {"actual": got}}, str(tmp_path)))
        cases.append(case_record(rec, test_id=f"test_ctrl[{i}]", procedure_id="test_ctrl", case=str(i)))
    record = run_record(cases)
    jsonschema.validate(record, SCHEMA)
    (proc,) = procedures(record)
    assert len(proc.variants) == 1
    assert proc.variants[0][0] == ["Unscoped", "   - Verify reg.CTRL & <MASK> == <wdata>", "   - Verify 'R' == 0x00F3"]
    text = render(record, "report", "text")
    assert "Verify reg.CTRL & 0x00FF == 0x00F3 -> got 0x12F0" in text
    assert "Verify reg.CTRL & 0xFF00 == 0x1200 -> got 0x12F3" in text


WIDTHS = '''\
def test_reset(verify, reg, wdata, mask, limit, width, kw):
    verify.equal(reg.read(), 0)
    verify.bits(reg.read(), 0x1, mask=0xF)
    verify.bits(reg.read(), 0xABF3, mask=0xFF, width=width)
    verify.less(reg.read(), 0x100, units="LSB")
    verify.between(reg.read(), 0x10, limit)
    verify.bits(reg.read(), wdata, **kw)
    verify.bits(*kw)
    verify.equal(reg.read(), 0xF3, radix="hex", width=16)
    verify.bits(reg.read(), 0xF3, mask=255, width=16)
    verify.equal(reg.read(), 0xF3, radix=kw["radix"], width=16)
    verify.equal(reg.read(), "idle")
'''


def _hex(value, width):
    digits = f"{value:0{width // 4}X}"
    head = len(digits) % 4
    groups = ([digits[:head]] if head else []) + [digits[i:i + 4] for i in range(head, len(digits), 4)]
    return "0x" + ("_".join(groups) if len(digits) > 4 else digits)


def _at_width(width, tmp_path):
    """The records of ``WIDTHS`` for a register ``width`` bits wide, whose values
    say so themselves (``__verify_format__``), as pytest-verifier makes them."""
    def h(value, bits=width):
        return _hex(value, bits)

    def d(line, kind, tail, **more):
        name = "reg.read()"
        return {"check_type": kind, "name": name, "auto_name": True, "description": f"Verify '{name}' {tail}",
                "actual": 0, "radix": "hex", "width": width, "rendered": {"actual": h(0)}, "passed": True,
                "location": f"test_reset.py:{line}", "phase": "call", **more}

    records = [
        d(2, "equal", f"== {h(0)}", expected=0),
        d(3, "bits", f"& {h(0xF)} == {h(1)}", expected=1, mask=0xF),
        d(4, "bits", f"& {h(0xFF)} == {h(0xF3)}", expected=0xABF3, mask=0xFF),
        d(5, "less", f"< {h(0x100)}LSB", threshold=0x100, units="LSB"),
        d(6, "between", f"∈ [{h(0x10)}, {h(0x20)}]", low=0x10, high=0x20),
        d(7, "bits", f"& {h(width)} == {h(0)}", expected=width, mask=width),
        d(8, "bits", f"& {h(width)} == {h(0)}", expected=width, mask=width),
        d(9, "equal", "== 0x00F3", expected=0xF3, width=16),
        d(10, "bits", "& 0x00FF == 0x00F3", expected=0xF3, mask=255, width=16),
        d(11, "equal", f"== {'0x00F3' if width == 8 else '0b1111_0011'}", expected=0xF3, width=16,
          radix="hex" if width == 8 else "bin"),
        d(12, "equal", "== 'idle'", expected="idle"),
    ]
    return [to_check(r, str(tmp_path)) for r in records]


def test_a_format_the_value_gives_does_not_split_the_procedure(tmp_path):
    """A literal is padded to the width of this run's value (``0x00`` at 8 bits,
    ``0x0000_0000`` at 32): the procedure shows it as the test wrote it, and what
    ``**kwargs`` pass by the parameter's name."""
    (tmp_path / "test_reset.py").write_text(WIDTHS)
    cases = [
        case_record(_recorded(*_at_width(width, tmp_path)), test_id=f"test_reset[r{width}]",
                    procedure_id="test_reset", case=f"r{width}")
        for width in (8, 16, 32)
    ]
    record = run_record(cases)
    jsonschema.validate(record, SCHEMA)
    (proc,) = procedures(record)
    assert len(proc.variants) == 1
    assert proc.variants[0][0][1:] == [
        "   - Verify reg.read() == 0",
        "   - Verify reg.read() & 0xF == 0x1",
        "   - Verify reg.read() & 0xFF == 0xABF3",  # ``width=width``: the test's literals, whole
        "   - Verify reg.read() < 0x100LSB",
        "   - Verify reg.read() ∈ [0x10, <limit>]",
        "   - Verify reg.read() & <mask> == <wdata>",  # the mask is in ``**kw``
        "   - Verify 'reg.read()' & <mask> == <expected>",  # everything is in ``*kw``
        # ``radix=`` and ``width=`` literals fix the format: as pytest-verifier shows it.
        "   - Verify reg.read() == 0x00F3",
        "   - Verify reg.read() & 0x00FF == 0x00F3",
        "   - Verify reg.read() == 0xF3",  # ``radix=`` is not a literal
        "   - Verify reg.read() == 'idle'",  # not an integer: no format applies to it
    ]
    # The report keeps the text of each run.
    text = render(record, "report", "text")
    assert "- Verify reg.read() == 0x00 -> got 0x00   ✓" in text
    assert "- Verify reg.read() == 0x0000_0000 -> got 0x0000_0000   ✓" in text
    assert "- Verify reg.read() & 0x0000_000F == 0x0000_0001 -> got 0x0000_0000   ✓" in text
    assert "- Verify reg.read() < 0x0100LSB -> got 0x0000   ✓" in text


LOOSE = '''\
def test_ctrl(verify, reg, wdata, mask, ok):
    verify.bits(reg.CTRL | 1, wdata, mask=mask | 3, width=16)
    verify.bits(reg.CTRL ^ 1, 0xABF3, mask=0x00FF, width=16)
    verify.bits(reg.CTRL | 1, 0x00F3, width=16)
    verify.bits(reg.A if ok else reg.B, 0x00F3, width=16)
    verify.bits(not reg.CTRL, 0x00F3, mask=0x00FF, width=16)
    verify.bits(reg.CTRL >> 4 & 0xF, 0x00F3, mask=0x00FF, width=16)
'''


@pytest.mark.parametrize(
    "line, masked, text, symbolic",
    [
        (2, True, "(reg.CTRL | 1) & 0x00FF == 0x00F3", "(reg.CTRL | 1) & <mask | 3> == <wdata>"),
        (3, True, "(reg.CTRL ^ 1) & 0x00FF == 0x00F3", None),
        # ``|`` binds tighter than ``==``: the line already says what is checked.
        (4, False, "reg.CTRL | 1 == 0x00F3", None),
        (5, False, "(reg.A if ok else reg.B) == 0x00F3", None),
        (6, True, "(not reg.CTRL) & 0x00FF == 0x00F3", None),
        (7, True, "reg.CTRL >> 4 & 0xF & 0x00FF == 0x00F3", None),
    ],
)
def test_a_checked_value_that_binds_loosely_is_in_parentheses(tmp_path, line, masked, text, symbolic):
    """``bits`` checks ``(<value> ^ <expected>) & <mask>``: without parentheses
    ``reg.CTRL | 1 & 0x00FF`` would state ``reg.CTRL | (1 & 0x00FF)``."""
    (tmp_path / "test_ctrl.py").write_text(LOOSE)
    name = LOOSE.splitlines()[line - 1].split("verify.bits(")[1].split(", ")[0]
    d = _bits(line, **({} if masked else NO_MASK))
    d.update(name=name, description=f"Verify '{name}' {'& 0x00FF ' if masked else ''}== 0x00F3")
    check = to_check(d, str(tmp_path))
    assert (check.text, check.symbolic_text) == (text, symbolic)
    record = _record(check)
    assert f"- Verify {text} -> got 0x12F0" in render(record, "report", "text")
    assert f"- Verify {symbolic or text}" in render(record, "procedure", "text")


def test_markup_in_the_texts_of_a_check_is_escaped(tmp_path):
    """The value's text is the producer's and field names are the user's: neither
    is markup in the HTML or the Markdown report."""
    name = "<b>EN</b> & `x`"
    d = {**BITS, "rendered": {"actual": "0x1\n<script>alert(1)</script>`x`|"},
         "bit_diff": {"bits": [[0, 0]], "text": "bit 0\ndiffers <img src=x onerror=1>",
                      "fields": [{"name": name, "msb": 0, "lsb": 0, "actual": "0b0", "expected": "0b1"}]}}
    check = to_check(d, str(tmp_path))
    assert check.actual_text == "0x1 <script>alert(1)</script>`x`|"  # one line
    assert check.difference == f"bit 0 differs <img src=x onerror=1>: {name} [0] is 0b0, expected 0b1"
    record = _record(check)
    result = f"got {check.actual_text}, {check.difference}"
    assert f"- Verify 'CTRL' & 0x00FF == 0x00F3 -> {result}   ✗" in render(record, "report", "text")
    html = _body(render(record, "report", "html"))
    assert (
        "<code>got 0x1 &lt;script&gt;alert(1)&lt;/script&gt;`x`|, bit 0 differs &lt;img src=x onerror=1&gt;: "
        "&lt;b&gt;EN&lt;/b&gt; &amp; `x` [0] is 0b0, expected 0b1</code>"
    ) in html
    for raw in ("<script", "<img", "<b>"):
        assert raw not in html
    md = render(record, "report", "md")
    # A code span fenced with more backticks than it holds: nothing in it is Markdown.
    assert f"→ `` {result} `` ✗" in md
    for fmt in ("text", "md", "html"):  # and none of it is in the procedure
        shown = _body(render(record, "procedure", fmt))
        assert "script" not in shown and "EN" not in shown and "differ" not in shown


def _item(i, actual, text, passed):
    d = {"check_type": "bits", "name": f"r{i}", "description": f"Verify 'r{i}' == 0x01", "actual": actual,
         "expected": 1, "radix": "hex", "width": 8, "rendered": {"actual": text, "expected": "0x01"},
         "passed": passed}
    if not passed:
        d["bit_diff"] = {"bits": [[1, 0]], "text": "bits [1:0] differ"}
    return d


def test_all_satisfy_folds_with_the_items_values_in_hex(tmp_path):
    kids = [_item(i, 2 if i == 1 else 1, "0x02" if i == 1 else "0x01", i != 1) for i in range(3)]
    for k in kids:
        k.update(name="r", description="Verify 'r' == 0x01")
    d = {"check_type": "all_satisfy", "name": "all", "passed": False, "child_checks": kids, "item_name": "x",
         "items_name": "regs", "description": "Verify all items in 'all' satisfy condition (3 items)"}
    check = to_check(d, str(tmp_path))
    assert check.each.values == [1, 2, 1] and check.each.texts == ["0x01", "0x02", "0x01"]
    record = _record(check)
    entry = record["tests"][0]["unscoped"][0]
    assert entry["each"] == {"count": 3, "failed": 1, "item": "x", "items": "regs", "values": [1, 2, 1],
                             "texts": ["0x01", "0x02", "0x01"]}
    assert entry["children"][0]["actual_text"] == "0x02" and entry["children"][0]["index"] == 1
    assert render(record, "report", "text").splitlines()[-2:] == [
        "   - Verify 'all': for each x in regs, 'r' == 0x01 -> 1 of 3 failed   ✗",
        "      - Verify item [1]: == 0x01 -> got 0x02, bits [1:0] differ   ✗",
    ]
    assert "→ `got 0x02, bits [1:0] differ` ✗" in render(record, "report", "md")
    assert "<code>got 0x02, bits [1:0] differ</code>" in render(record, "report", "html")
    assert render(record, "procedure", "text").splitlines()[-1] == "   - Verify 'all': for each x in regs, 'r' == 0x01"
    # Some items without a text of their own; none at all: no ``texts``.
    del kids[0]["rendered"]
    assert to_check(d, str(tmp_path)).each.texts == [None, "0x02", "0x01"]
    jsonschema.validate(run_record([case_record(_recorded(to_check(d, str(tmp_path))))]), SCHEMA)
    for k in kids:
        k.pop("rendered", None)
    plain = to_check(d, str(tmp_path))
    assert plain.each.texts is None
    assert "texts" not in _record(plain)["tests"][0]["unscoped"][0]["each"]


def test_a_long_list_keeps_the_texts_of_the_values_it_keeps(tmp_path):
    kids = [{**_item(0, i, f"0x{i:02X}", True), "name": "r", "description": "Verify 'r' == 0x01"} for i in range(70)]
    d = {"check_type": "all_satisfy", "name": "all", "passed": True, "child_checks": kids,
         "description": "Verify all items in 'all' satisfy condition (70 items)"}
    each = to_check(d, str(tmp_path)).each
    assert each.values == list(range(32)) + list(range(38, 70))
    assert each.texts == [f"0x{i:02X}" for i in each.values]


def _pass(text, value, passed=True, at=None):
    from stepdoc import Location

    return Check("dev.map.adc.value == <x>", passed, actual=value, actual_text=text,
                 difference=None if passed else "bits [1:0] differ",
                 location=None if at is None else Location(__file__, at))


def test_a_collapsed_loop_shows_the_last_pass(dev, rec):
    with step("Poll"):
        for value in (1, 2, 3):
            dev.map.ctrl.mode = 1
            rec.add_check(_pass(f"0x{value:02X}", value))
    (loop,) = rec.steps[0].entries
    kept = loop.entries[1]
    assert loop.count == 3 and (kept.actual, kept.actual_text, kept.difference) == (3, "0x03", None)
    assert "      1.1.2. Verify dev.map.adc.value == <x> -> got 0x03   ✓" in report(rec).splitlines()


def test_a_titled_repeat_shows_the_last_pass_and_a_failing_pass_its_own(dev, rec):
    with repeat("Drain", until="FIFO is empty"):
        for value in (1, 2, 3):
            dev.map.ctrl.mode = 1
            rec.add_check(_pass(f"0x{value:02X}", value))
    with step("Check every word"):
        for value in (1, 1, 2):
            dev.map.ctrl.mode = 1
            rec.add_check(_pass(f"0x{value:02X}", value, passed=value == 1))
    lines = report(rec).splitlines()
    assert "   1.2. Verify dev.map.adc.value == <x> -> got 0x03   ✓" in lines
    assert rec.steps[0].loop.count == 3
    # The two passes that passed are one loop; the one that failed follows with its own value.
    assert "      2.1.2. Verify dev.map.adc.value == <x> -> got 0x01   ✓" in lines
    assert "   2.3. Verify dev.map.adc.value == <x> -> got 0x02, bits [1:0] differ   ✗" in lines
    jsonschema.validate(run_record([case_record(rec)]), SCHEMA)
    assert all("0x0" not in line for line in procedure(rec))


def test_dry_run_keeps_the_text_of_a_value(dev):
    with Recorder(dry_run=True) as rec:
        rec.add_check(_pass("0x02", 2, passed=False))
    assert _shown(rec) == ["- Verify dev.map.adc.value == <x> -> got 0x02   ·"]


def test_run_records_of_every_schema_version_load_and_render(tmp_path):
    record = _record(to_check(EQUAL, str(tmp_path)), to_check(BITS, str(tmp_path)))
    path = tmp_path / "run.json"
    path.write_text(dumps(record), encoding="utf-8")
    loaded = load(path)
    assert loaded == record
    assert "- Verify 'R' == 0x00F3 -> got 0x00F0, bits [1:0] differ   ✗" in render(loaded, "report", "text")
    # Older records have neither field: the value is rendered by stepdoc, as it was.
    for version in ("0.1", "0.2", "0.3", "0.4"):
        old = json.loads(dumps(record))
        old["schema_version"] = version
        for entry in old["tests"][0]["unscoped"]:
            del entry["actual_text"], entry["difference"]
        path.write_text(json.dumps(old), encoding="utf-8")
        loaded = load(path)
        jsonschema.validate(loaded, SCHEMA)
        assert "- Verify 'R' == 0x00F3 -> got 240   ✗" in render(loaded, "report", "text")
        assert "→ `got 4848` ✗" in render(loaded, "report", "md")
        assert "<code>got 4848</code>" in render(loaded, "report", "html")
    old["schema_version"] = "0.5"
    path.write_text(json.dumps(old), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported run record schema '0.5'"):
        load(path)


def test_the_schema_types_the_new_fields(tmp_path):
    record = _record(to_check(EQUAL, str(tmp_path)))
    entry = record["tests"][0]["unscoped"][0]
    for key in ("actual_text", "difference"):
        bad = json.loads(json.dumps(record))
        bad["tests"][0]["unscoped"][0][key] = 240
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, SCHEMA)
    entry["each"] = {"count": 1, "failed": 0, "item": None, "items": None, "values": [1], "texts": [1]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(record, SCHEMA)
    entry["each"]["texts"] = ["0x01", None]
    jsonschema.validate(record, SCHEMA)


# --------------------------------------------------------------------------- #
# The real pytest-verifier (skipped without it, or before it had ``bits``)
# --------------------------------------------------------------------------- #

try:
    import pytest_verifier
except ImportError:  # stepdoc's own environment
    pytest_verifier = None

real = pytest.mark.skipif(
    not hasattr(getattr(pytest_verifier, "Verify", None), "bits"),
    reason="needs a pytest-verifier with verify.bits (0.11)",
)

# A register map: reading ``<reg>.value`` is recorded, like a bridge would.
REGMAP = '''
import stepdoc


class Reg:
    def __init__(self, path, values):
        self._path, self._values = path, list(values)

    @property
    def value(self):
        v = self._values.pop(0) if len(self._values) > 1 else self._values[0]
        stepdoc.record_action(self._path, "read", v)
        return v


class Model:
    """A register whose value says how it is shown."""

    def __init__(self, value):
        self._value = value

    def __eq__(self, other):
        return self._value == other

    def __verify_format__(self):
        return {"radix": "hex", "width": 16, "fields": {"EN": 0, "MODE": (3, 1)}, "value": self._value}


class Dev:
    def __init__(self, **regs):
        for name, values in regs.items():
            setattr(self, name, Reg(f"dev.{name}", values))
'''


@pytest.fixture
def project(pytester):
    pytester.makepyfile(regmap=REGMAP)
    pytester.makeini("[pytest]\nstepdoc_skip_modules = regmap\n")
    return pytester


def run(pytester, *args):
    out = ("--stepdoc-record=run.json", "--stepdoc-procedure=procedure.txt", "--stepdoc-report=report.txt")
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", *out, *args)


def lines(pytester, name):
    return [x.rstrip() for x in (pytester.path / name).read_text(encoding="utf-8").splitlines()]


def record_of(pytester):
    record = json.loads((pytester.path / "run.json").read_text(encoding="utf-8"))
    jsonschema.validate(record, SCHEMA)
    return record


READBACK = '''
import pytest
from regmap import Dev, Model
from stepdoc import step

MASK = 0x00FF
FIELDS = {"EN": 0, "MODE": (3, 1)}


@pytest.mark.parametrize("wdata", [0xABF3, 0x12F0])
def test_readback(verify, wdata):
    dev = Dev(CTRL=[0x12F0])
    with step("Read the control word back"):
        verify.equal(dev.CTRL.value, wdata, radix="hex", width=16)
        verify.bits(dev.CTRL.value, wdata, mask=MASK, fields=FIELDS, width=16)
        verify.bits(dev.CTRL.value, wdata, mask=0x00FF, width=16, name="Low byte")
        verify.bits(dev.CTRL.value, 0xABF3, mask=MASK, width=16)
        verify.bits(dev.CTRL.value, wdata, width=16)
        rdata = dev.CTRL.value
        verify.bits(rdata, wdata, mask=MASK, width=16,
                    actual_name=f"dev.CTRL[{wdata & 3}]", expected_name="wdata")
    with step("Limits"):
        verify.less(rdata, 0x100, radix="hex")
        verify.between(5, 8, 12, radix="bin", width=4, name="N")
        verify.equal(Model(0xF0), 0xF3, name="Model")
'''


@real
def test_registers_with_the_real_plugin(project):
    project.makepyfile(test_readback=READBACK)
    run(project).assert_outcomes(failed=2)
    # One procedure for both cases: no value of a run is in it.
    assert lines(project, "procedure.txt") == [
        "test_readback.py::test_readback",
        "Parameters:",
        "  wdata   44019, 4848",
        "1. Read the control word back",
        "   1.1. Verify dev.CTRL == <wdata>",
        "   1.2. Verify dev.CTRL & <MASK> == <wdata>",
        "   1.3. Verify 'Low byte': dev.CTRL & 0x00FF == <wdata>",
        "   1.4. Verify dev.CTRL & <MASK> == 0xABF3",
        "   1.5. Verify dev.CTRL == <wdata>",
        "   1.6. Read dev.CTRL -> <rdata>",
        "   1.7. Verify dev.CTRL[<wdata & 3>] & <MASK> == <wdata>",
        "2. Limits",
        "   2.1. Verify rdata < 0x100",
        "   2.2. Verify 'N' ∈ [0b1000, 0b1100]",
        # The value gives this format: the literal reads as the test wrote it.
        "   2.3. Verify 'Model': Model(0xF0) == 0xF3",
    ]
    report = lines(project, "report.txt")
    fields = "EN [0] is 0b0, expected 0b1; MODE [3:1] is 0b000, expected 0b001"
    first = report[: report.index("")]
    assert [x.strip() for x in first[3:]] == [
        "1.1. Verify dev.CTRL == 0xABF3 -> got 0x12F0, bit 15, bits [13:11], bit 8 and bits [1:0] differ   ✗",
        f"1.2. Verify dev.CTRL & 0x00FF == 0x00F3 -> got 0x12F0, bits [1:0] differ: {fields}   ✗",
        "1.3. Verify 'Low byte': dev.CTRL & 0x00FF == 0x00F3 -> got 0x12F0, bits [1:0] differ   ✗",
        "1.4. Verify dev.CTRL & 0x00FF == 0x00F3 -> got 0x12F0, bits [1:0] differ   ✗",
        "1.5. Verify dev.CTRL == 0xABF3 -> got 0x12F0, bit 15, bits [13:11], bit 8 and bits [1:0] differ   ✗",
        "1.6. Read dev.CTRL -> 4848",
        "1.7. Verify dev.CTRL[3] & 0x00FF == wdata (0x00F3) -> got 0x12F0, bits [1:0] differ   ✗",
        "2. Limits   ✗",
        "2.1. Verify rdata < 0x100 -> got 0x12F0   ✗",
        "2.2. Verify 'N' ∈ [0b1000, 0b1100] -> got 0b0101   ✗",
        f"2.3. Verify 'Model': Model(0xF0) == 0x00F3 -> got 0x00F0, bits [1:0] differ: {fields}   ✗",
    ]
    second = report[report.index("") + 1:]
    assert "   1.2. Verify dev.CTRL & 0x00FF == 0x00F0 -> got 0x12F0   ✓" in second
    assert "   1.7. Verify dev.CTRL[0] & 0x00FF == wdata (0x00F0) -> got 0x12F0   ✓" in second
    failed, passed = record_of(project)["tests"]
    check = failed["steps"][0]["entries"][1]
    assert check["text"] == "dev.CTRL & 0x00FF == 0x00F3"
    assert check["symbolic_text"] == "dev.CTRL & <MASK> == <wdata>"
    assert (check["actual"], check["actual_text"]) == (4848, "0x12F0")
    assert check["difference"] == f"bits [1:0] differ: {fields}"
    assert [r["target"]["concrete"] for r in check["reads"]] == ["dev.CTRL"]
    named = failed["steps"][0]["entries"][6]
    assert named["operands"] == {
        "actual": {"concrete": "dev.CTRL[3]", "symbolic": "dev.CTRL[<wdata & 3>]"},
        "expected": {"concrete": "wdata", "symbolic": "wdata"},
    }
    assert failed["steps"][0]["entries"][2]["label"] == "Low byte"
    assert "difference" not in passed["steps"][0]["entries"][1]
    # A value with ``__verify_format__``: the record holds its integer.
    model = failed["steps"][1]["entries"][2]
    assert (model["actual"], model["actual_text"]) == (240, "0x00F0")


@real
def test_markdown_and_html_with_the_real_plugin(project):
    project.makepyfile(test_readback=READBACK)
    args = ("-p", "no:cacheprovider", "-k", "44019")
    project.runpytest_subprocess(*args, "--stepdoc-procedure=procedure.md", "--stepdoc-report=report.md")
    project.runpytest_subprocess(*args, "--stepdoc-procedure=procedure.html", "--stepdoc-report=report.html")
    difference = "bits [1:0] differ: EN [0] is 0b0, expected 0b1; MODE [3:1] is 0b000, expected 0b001"
    md = (project.path / "report.md").read_text(encoding="utf-8")
    assert f"- 1.2. Verify `dev.CTRL & 0x00FF == 0x00F3` → `got 0x12F0, {difference}` ✗" in md
    assert "- 1.3. Verify 'Low byte': `dev.CTRL & 0x00FF == 0x00F3` → `got 0x12F0, bits [1:0] differ` ✗" in md
    html = (project.path / "report.html").read_text(encoding="utf-8")
    assert (
        '<code class="expr">dev.<wbr>CTRL &amp; 0x00FF == 0x00F3</code> '
        f'<span class="result">→ <code>got 0x12F0, {difference}</code></span>'
    ) in html
    assert "- 1.2. Verify `dev.CTRL & <MASK> == <wdata>`" in (project.path / "procedure.md").read_text(encoding="utf-8")
    page = _body((project.path / "procedure.html").read_text(encoding="utf-8"))
    assert '<code class="expr">dev.<wbr>CTRL &amp; &lt;MASK&gt; == &lt;wdata&gt;</code>' in page
    assert "0x12F0" not in page and "differ" not in page


@real
def test_loops_with_the_real_plugin(project):
    """pytest-verifier's checks arrive when the phase ends, so every pass of a loop
    has its own line, with the value and the difference of that pass."""
    project.makepyfile(
        test_loops='''
        from regmap import Dev
        from stepdoc import repeat, step

        IDLE = 0x10


        def test_loops(verify):
            dev = Dev(STATUS=[0x11, 0x12, 0x13], FIFO=[0x31, 0x32, 0x33], WORD=[0x10, 0x10, 0x14])
            with step("Wait"):
                for _ in range(3):
                    verify.bits(dev.STATUS.value, IDLE, mask=0xF0, width=8)
            with repeat("Drain", until="FIFO is empty"):
                for _ in range(3):
                    verify.bits(dev.FIFO.value, 0x30, mask=0xF0, width=8)
            with step("Words"):
                for _ in range(3):
                    verify.equal(dev.WORD.value, IDLE, radix="hex", width=8)
        '''
    )
    run(project).assert_outcomes(failed=1)
    procedure = lines(project, "procedure.txt")
    assert procedure[1:5] == ["1. Wait", *[f"   1.{i}. Verify dev.STATUS & 0xF0 == <IDLE>" for i in (1, 2, 3)]]
    assert procedure[5:7] == ["2. Drain (repeat until FIFO is empty)", "   2.1. Verify dev.FIFO & 0xF0 == 0x30"]
    assert procedure[-3:] == [f"   3.{i}. Verify dev.WORD == <IDLE>" for i in (1, 2, 3)]
    report = lines(project, "report.txt")
    assert report[2:5] == [
        f"   1.{i}. Verify dev.STATUS & 0xF0 == 0x10 -> got 0x1{i}   ✓" for i in (1, 2, 3)
    ]
    assert [x.split(" -> ")[1] for x in report[6:9]] == ["got 0x31   ✓", "got 0x32   ✓", "got 0x33   ✓"]
    assert report[-3:] == [
        "   3.1. Verify dev.WORD == 0x10 -> got 0x10   ✓",
        "   3.2. Verify dev.WORD == 0x10 -> got 0x10   ✓",
        "   3.3. Verify dev.WORD == 0x10 -> got 0x14, bit 2 differs   ✗",
    ]
    steps = record_of(project)["tests"][0]["steps"]
    assert steps[1]["loop"]["count"] == 3
    assert [e.get("difference") for e in steps[2]["entries"]] == [None, None, "bit 2 differs"]


@real
def test_all_satisfy_with_the_real_plugin(project):
    project.makepyfile(
        test_bank='''
        from stepdoc import step

        RESET = 0x01


        def test_bank(verify):
            bank = [0x01, 0x02, 0x01]
            with step("Every register is at its reset value"):
                verify.all_satisfy(bank, lambda r: verify.bits(r, RESET, width=8, name="reset"), name="Bank")
        '''
    )
    run(project).assert_outcomes(failed=1)
    line = "Verify 'Bank': for each r in bank, r == <RESET>"
    assert lines(project, "procedure.txt")[-1] == f"   1.1. {line}"
    assert lines(project, "report.txt")[-2:] == [
        "   1.1. Verify 'Bank': for each r in bank, r == 0x01 -> 1 of 3 failed   ✗",
        "      - Verify item [1]: r == 0x01 -> got 0x02, bits [1:0] differ   ✗",
    ]
    check = record_of(project)["tests"][0]["steps"][0]["entries"][0]
    assert check["each"] == {"count": 3, "failed": 1, "item": "r", "items": "bank", "values": [1, 2, 1],
                             "texts": ["0x01", "0x02", "0x01"]}
    assert check["children"][0]["difference"] == "bits [1:0] differ"


@real
def test_registers_of_several_widths_share_one_procedure(project):
    """The format a value gives (``__verify_format__``), a ``width=`` that is a
    parameter and a mask in ``**kwargs`` are this run's: none of them is in the
    procedure, so the cases are one procedure."""
    project.makepyfile(
        test_widths='''
        import pytest
        from stepdoc import step


        class Reading(int):
            def __new__(cls, value, width):
                self = super().__new__(cls, value)
                self.width = width
                return self

            def __verify_format__(self):
                return {"radix": "hex", "width": self.width}


        class Reg:
            def __init__(self, width):
                self.width = width

            def read(self):
                return Reading(0, self.width)


        @pytest.mark.parametrize("reg", [Reg(8), Reg(16), Reg(32)], ids=["r8", "r16", "r32"])
        def test_reset_value(verify, reg):
            width = reg.width
            kw = {"mask": 1 << (width - 1), "width": width}
            with step("Read the reset value"):
                verify.equal(reg.read(), 0)
                verify.bits(reg.read(), 0x1, mask=0xF)
                verify.bits(int(reg.read()), 0x0, mask=0xFF, width=width)
                verify.bits(int(reg.read()), 0, **kw)
                verify.bits(int(reg.read()) | 1, 0x01, mask=0x0F, width=8)
        '''
    )
    run(project).assert_outcomes(failed=3)
    assert lines(project, "procedure.txt")[3:] == [
        "1. Read the reset value",
        "   1.1. Verify reg.read() == 0",
        "   1.2. Verify reg.read() & 0xF == 0x1",
        "   1.3. Verify int(reg.read()) & 0xFF == 0x0",
        "   1.4. Verify int(reg.read()) & <mask> == 0",
        "   1.5. Verify (int(reg.read()) | 1) & 0x0F == 0x01",
    ]
    report = [x.strip() for x in lines(project, "report.txt")]
    assert "1.1. Verify reg.read() == 0x00 -> got 0x00   ✓" in report
    assert "1.1. Verify reg.read() == 0x0000_0000 -> got 0x0000_0000   ✓" in report
    assert "1.2. Verify reg.read() & 0x000F == 0x0001 -> got 0x0000, bit 0 differs   ✗" in report
    assert "1.4. Verify int(reg.read()) & 0x8000 == 0x0000 -> got 0x0000   ✓" in report
    assert "1.5. Verify (int(reg.read()) | 1) & 0x0F == 0x01 -> got 0x01   ✓" in report
    assert [c.get("symbolic_text") for c in record_of(project)["tests"][0]["steps"][0]["entries"]] == [
        "reg.read() == 0",
        "reg.read() & 0xF == 0x1",
        "int(reg.read()) & 0xFF == 0x0",
        "int(reg.read()) & <mask> == 0",
        None,
    ]
