"""SYM-8: variable parts of a target, put back into the bridge's concrete name."""

import ast

import pytest

from stepdoc.core.symbolic import apply_target_pattern, target_pattern


def _expr(src: str) -> ast.expr:
    node = ast.parse(src, mode="eval").body
    return node


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ('getattr(tb.dut.block, f"reg{ch}")', "tb.dut.block.reg<ch>"),
        ("tb.dut.regs[i].value", "tb.dut.regs[<i>].value"),
        ('tb.dut.regs["a"].value', None),
        ("tb.dut.block.reg1", None),
        ('getattr(tb.dut, "block")', None),
    ],
)
def test_target_pattern(src, expected):
    assert target_pattern(_expr(src)) == expected


@pytest.mark.parametrize(
    ("concrete", "pattern", "expected"),
    [
        ("dut.block.reg3.value", "tb.dut.block.reg<ch>.value", "dut.block.reg<ch>.value"),
        ("dut.block.reg3", "tb.dut.block.reg<ch>", "dut.block.reg<ch>"),
        ("dut.blk_a.reg_7", "dut.blk_<x>.reg_<y>", "dut.blk_<x>.reg_<y>"),
        ("dut.block.mode", "tb.dut.block.reg<ch>", None),
        ("dut.other.reg3", "tb.dut.block.reg<ch>", None),
        ("dut.block.reg3", "tb.dut.block.reg1", None),
        ("chip.block.reg3", "tb.dut.block.reg<ch>", "chip.block.reg<ch>"),
        ("dut.regs[2].value", "tb.dut.regs[<i>].value", "dut.regs[<i>].value"),
    ],
)
def test_apply_target_pattern(concrete, pattern, expected):
    assert apply_target_pattern(concrete, pattern) == expected
