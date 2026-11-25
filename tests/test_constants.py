"""Named constants show their value in the procedure (SYM-9).

A module-level or imported upper-case name (``MY_CONST``, ``consts.LIMIT``) is the
same in every run, so the procedure shows its value where a parameter or a local
variable reads ``<name>``."""

import ast
import inspect
import re

import pytest

from stepdoc import step
from stepdoc.core.symbolic import is_constant
from tests import consts_module
from tests.consts_module import LIMIT
from tests.helpers import procedure, report

MY_CONST = 100
MODE = "fast"
API_TOKEN = "s3cret"
lower_case = 7


def test_writes_show_a_constants_value(dev, rec):
    level = 5
    with step("Configure"):
        dev.map.pulse.width = MY_CONST
        dev.map.pulse.period = LIMIT
        dev.map.dac.level = consts_module.Cfg.MAX
        dev.map.adc.value = MY_CONST + 1
        dev.map.ctrl.mode = MODE
        dev.map.ctrl.enable = API_TOKEN
        dev.map.status.pll_locked = lower_case
        dev.map.dac.level = MY_CONST * level

    assert procedure(rec) == [
        "1. Configure",
        "   1.1. dev.map.pulse.width = 100",
        "   1.2. dev.map.pulse.period = 300",
        "   1.3. dev.map.dac.level = 4095",
        "   1.4. dev.map.adc.value = 101",
        '   1.5. dev.map.ctrl.mode = "fast"',
        "   1.6. dev.map.ctrl.enable = <API_TOKEN>",
        "   1.7. dev.map.status.pll_locked = <lower_case>",
        "   1.8. dev.map.dac.level = <MY_CONST * level>",
    ]
    assert "   1.1. dev.map.pulse.width = 100" in report(rec)


def test_a_parameter_or_local_with_a_constant_name_still_varies(dev, rec):
    def helper(MAX_WIDTH):
        LOCAL = MAX_WIDTH
        with step("Set"):
            dev.map.pulse.width = MAX_WIDTH
            dev.map.pulse.period = LOCAL

    helper(9)
    assert procedure(rec)[1:] == [
        "   1.1. dev.map.pulse.width = <MAX_WIDTH>",
        "   1.2. dev.map.pulse.period = <LOCAL>",
    ]


def _tree_of(fn):
    """``fn``'s source parsed with parent links, as executing gives it."""
    import executing

    return executing.Source.for_filename(inspect.getsourcefile(fn)).tree


def _expr_in(tree, text, func):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            for n in ast.walk(node):
                if isinstance(n, ast.expr) and ast.unparse(n) == text:
                    return n
    raise LookupError(text)


def _shapes(cfg, OTHER, items):  # noqa: N803 - a parameter in upper case on purpose
    import tests.consts_module as imported
    from tests.consts_module import LIMIT as LOCAL_LIMIT

    for LOOP in items:  # noqa: N806
        pass
    return [
        MY_CONST, consts_module.Cfg.MAX, -MY_CONST, MY_CONST + 1, (LIMIT - 1) * 2, imported.LIMIT,
        LOCAL_LIMIT, cfg.MAX, OTHER, LOOP, lower_case, consts_module.lower, MY_CONST * cfg,
        [x for x in items], [MY_CONST for _ in items], [COMP for COMP in items], 1 + 2,
    ]


@pytest.mark.parametrize(
    "text, constant",
    [
        ("MY_CONST", True),
        ("consts_module.Cfg.MAX", True),
        ("-MY_CONST", True),
        ("MY_CONST + 1", True),
        ("(LIMIT - 1) * 2", True),
        ("imported.LIMIT", True),
        ("LOCAL_LIMIT", True),
        ("cfg.MAX", False),
        ("OTHER", False),
        ("LOOP", False),
        ("lower_case", False),
        ("consts_module.lower", False),
        ("MY_CONST * cfg", False),
        ("COMP", False),
        ("1 + 2", False),
    ],
)
def test_what_counts_as_a_constant(text, constant):
    tree = _tree_of(_shapes)
    assert is_constant(_expr_in(tree, text, "_shapes")) is constant
    assert all(is_constant(_expr_in(tree, "MY_CONST", "_shapes")) for _ in range(2))  # cached


def test_a_node_without_parent_links_is_not_a_constant():
    assert is_constant(ast.parse("MY_CONST", mode="eval").body) is False


DEVICE = '''
import stepdoc


class Regs:
    def __setattr__(self, name, value):
        stepdoc.record_action(f"dev.{name}", "write", value)
'''

TESTS = '''
import pytest
import regs
from stepdoc import step

GAIN = 2


class Bench:
    level = 7


@pytest.fixture
def dev():
    return regs.Regs()


@pytest.fixture(params=[4])
def lanes(request):
    return request.param


def configure(dev, code):
    dev.code = code


@pytest.mark.parametrize("channel", [3])
def test_writes(dev, channel, lanes):
    width = 50
    period = width * channel
    bench = Bench()
    with step("Configure"):
        dev.width = width
        dev.period = period
        dev.level = bench.level
        dev.gain = GAIN * 2
        dev.lanes = lanes
        dev.mode = {"lanes": width, "on": True}
        dev.name = f"ch{channel}"
        configure(dev, width)
        for i in [1]:
            dev.index = i
'''


def test_only_parameters_stay_generic_in_written_values(pytester):
    """SYM-9 with the pytest plugin: it knows the test's parameters."""
    pytester.makepyfile(regs=DEVICE, test_writes=TESTS)
    pytester.makeini("[pytest]\nstepdoc_skip_modules = regs\n")
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "--stepdoc-procedure=procedure.txt")
    result.assert_outcomes(passed=1)
    text = (pytester.path / "procedure.txt").read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines() if re.match(r"\s+\d", line)]
    assert lines == [
        "1.1. dev.width = 50",
        "1.2. dev.period = <period>",
        "1.3. dev.level = 7",
        "1.4. dev.gain = 4",
        "1.5. dev.lanes = <lanes>",
        '1.6. dev.mode = {"lanes": 50, "on": True}',
        '1.7. dev.name = "ch<channel>"',
        "1.8. dev.code = <code>",
        "1.9. dev.index = <i>",
    ]
