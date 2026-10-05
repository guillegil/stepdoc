"""Hardware example: device register reads and writes."""

from stepdoc import look_through, step
from stepdoc.renderers.text import render_procedure, render_report


@step("Wait for PLL lock")
def wait_pll_lock(dev):
    while not dev.map.status.pll_locked:
        pass


def set_level(dev, code):
    dev.map.dac.level = code


@look_through
def set_level_lt(dev, code):
    dev.map.dac.level = code


@look_through
def configure(dev, w):
    set_level_lt(dev, w * 2)


def test_output_by_mode(dev, rec):
    mode, level, width = 2, 2048, 50
    with step("Select mode and level"):
        dev.map.ctrl.mode = mode
        dev.map.dac.level = level
        dev.map.pulse.width = 100
        dev.map.pulse.period = width * 4
    wait_pll_lock(dev)
    with step("Measure output"):
        vout = dev.map.adc.value
        assert vout == level // 2

    assert render_procedure(rec).splitlines() == [
        "1. Select mode and level",
        "   - dev.map.ctrl.mode = <mode>",
        "   - dev.map.dac.level = <level>",
        "   - dev.map.pulse.width = 100",
        "   - dev.map.pulse.period = <width * 4>",
        "2. Wait for PLL lock",
        "   - Read dev.map.status.pll_locked",
        "   - Read dev.map.status.pll_locked",
        "   - Read dev.map.status.pll_locked",
        "3. Measure output",
        "   - Read dev.map.adc.value -> <vout>",
    ]
    report = render_report(rec)
    assert "   - dev.map.dac.level = 2048" in report
    assert "   - Read dev.map.adc.value -> 1024" in report


def test_helpers(dev, rec):
    dac_code = 7
    set_level(dev, dac_code)
    set_level_lt(dev, dac_code)
    set_level_lt(dev, code=dac_code + 1)
    configure(dev, dac_code)
    rec.resolve()
    assert [a.symbolic_value for a in rec.unscoped] == [
        "<code>",  # default: the helper's own expression
        "<dac_code>",  # look-through
        "<dac_code + 1>",
        "<dac_code * 2>",  # two levels of look-through
    ]


def test_method_call_value_from(dev, rec):
    w = 12
    dev.write("pulse.width", w)
    dev.write("pulse.width", value=w + 1)
    rec.resolve()
    assert [a.symbolic_value for a in rec.unscoped] == ["<w>", "<w + 1>"]


def test_assignment_forms(dev, rec):
    a, b = 1, 2
    dev.map.pulse.width += a
    dev.map.pulse.width, dev.map.pulse.period = a, b
    dev.map.pulse.width: int = b
    rec.resolve()
    writes = [x for x in rec.unscoped if x.op == "write"]
    assert [x.symbolic_value for x in writes] == [
        "<dev.map.pulse.width + a>",
        "<a>",
        "<b>",
        "<b>",
    ]


def test_lambda_and_comprehension(dev, rec):
    setw = lambda v: setattr(dev.map.pulse, "width", v)  # noqa: E731
    setw(5)
    widths = [10, 20]
    [dev.write("pulse.width", x) for x in widths]
    rec.resolve()
    vals = [x.symbolic_value for x in rec.unscoped]
    # setattr() is a C function, so it has no frame: the "callee" used to bind
    # argument names is Block.__setattr__(self, name, value). Its third
    # parameter happens to be called `value` too, so this works by luck.
    assert vals == ["<v>", "<x>", "<x>"]


def test_exec_without_source_falls_back(dev, rec):
    code = compile("dev.map.pulse.width = n", "<generated>", "exec")
    exec(code, {"dev": dev, "n": 9})
    rec.resolve()
    (a,) = rec.unscoped
    assert a.symbolic is False
    assert a.value == 9
    assert "9  (concrete)" in render_procedure(rec)


def test_unscoped_actions_are_kept(dev, rec):
    dev.map.ctrl.enable = 1
    with step("Scoped"):
        dev.map.ctrl.enable = 0
    assert len(rec.unscoped) == 1 and len(rec.steps[0].actions) == 1
