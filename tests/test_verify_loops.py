"""ACT-10 for pytest-verifier: checks made in every pass of a collapsed loop fold
into its one shown pass, as live checks do.

Verify's checks arrive at the end of the phase, after the loop was collapsed;
the adapter then folds the passing checks of one call site into one check with
the last pass's values. A check that failed in some pass is shown for every
pass, after the pass. pytest-verifier is not installed here: the fake of
``test_verify_text`` publishes descriptors shaped like its own."""

import json

import jsonschema

from stepdoc.adapters._source import loop_at
from tests.test_adapters import SCHEMA, VERIFY_SPECS
from tests.test_verify_text import FAKE_VERIFY, lines, numbered, run

pytest_plugins = ["pytester"]

# Registers that record their reads and writes, like the bridges of a device.
CONFTEST = '''
import pytest

import stepdoc


class Reg:
    def __init__(self, path, values=None):
        self.__dict__["path"] = path
        self.__dict__["values"] = values or {}

    def __getattr__(self, name):
        value = self.values.get(name, 0)
        stepdoc.record_action(f"{self.path}.{name}", "read", value)
        return value

    def __setattr__(self, name, value):
        stepdoc.record_action(f"{self.path}.{name}", "write", value, value_from=("value",))


class Dev:
    def __init__(self):
        self.fifo = Reg("dev.fifo", {"flag": 0})

    def da_read(self, addr):
        stepdoc.record_action("dev.da", "read", 7)
        return 7


@pytest.fixture
def dev():
    return Dev()
'''


def make(pytester, test, ini=""):
    pytester.makepyfile(pytest_verifier=FAKE_VERIFY, verify_specs=VERIFY_SPECS, test_it=test)
    pytester.makeconftest(CONFTEST)
    pytester.makeini(f"[pytest]\n{ini}")
    return pytester


def entries(pytester, step=0):
    record = json.loads((pytester.path / "run.json").read_text())
    jsonschema.validate(record, SCHEMA)
    return record["tests"][0]["steps"][step]["entries"]


TITLED = '''
from stepdoc import repeat, step

def test_words(dev, verify):
    words = [0x11, 0x22, 0x33, 0x44, 0x55, 0x66]
    with step("Write words"):
        with repeat("Each word"):
            for i in range(len(words)):
                dev.fifo.data = words[i]
                got = dev.da_read(0x10)
                verify.equal(got, 7, name="Readback")
'''


def test_titled_repeat_shows_one_check(pytester):
    run(make(pytester, TITLED)).assert_outcomes(passed=1)
    assert numbered(lines(pytester, "report.txt")) == [
        "1. Write words   ✓",
        "   1.1. Each word (repeat)   ✓ 6 times",
        "      1.1.1. dev.fifo.data = 17, 34, … 102",
        "      1.1.2. Read dev.da -> 7",
        "      1.1.3. Verify 'Readback': got == 7 -> got 7   ✓",
    ]
    procedure = numbered(lines(pytester, "procedure.txt"))
    assert procedure[-1] == "      1.1.3. Verify 'Readback': got == 7"
    (loop,) = entries(pytester)
    assert [e["type"] for e in loop["entries"]] == ["action", "action", "check"]


def test_loop_that_collapsed_on_its_own_takes_its_checks(pytester):
    test = '''
    from stepdoc import step

    def test_it(dev, verify):
        words = [1, 2, 3, 4]
        with step("Auto"):
            for i in range(4):
                dev.fifo.data = i
                got = dev.da_read(0x10)
                verify.equal(got, 7, name="A")
                verify.between(got, words[i], 9, name="B")
            dev.fifo.data = 99
            verify.equal(got, 7, name="After")
    '''
    run(make(pytester, test)).assert_outcomes(passed=1)
    assert numbered(lines(pytester, "report.txt")) == [
        "1. Auto   ✓",
        "   1.1. Repeat   4 times, 0 ms",
        "      1.1.1. dev.fifo.data = 0, 1, 2, 3",
        "      1.1.2. Read dev.da -> 7",
        "      1.1.3. Verify 'A': got == 7 -> got 7   ✓",
        "      1.1.4. Verify 'B': got ∈ [4, 9] -> got 7   ✓",  # the last pass's values
        "   1.2. dev.fifo.data = 99",
        "   1.3. Verify 'After': got == 7 -> got 7   ✓",
    ]
    procedure = numbered(lines(pytester, "procedure.txt"))
    assert procedure[5] == "      1.1.4. Verify 'B': got ∈ [<words[i]>, 9]"


def test_untitled_repeat_check_takes_the_read_on_its_line(pytester):
    """ACT-5: the read the pass holds feeds the one check standing for all passes."""
    test = '''
    from stepdoc import repeat, step

    def test_it(dev, verify):
        with step("Untitled"):
            with repeat(until="done"):
                for i in range(4):
                    dev.fifo.data = i
                    verify.equal(dev.da_read(0x10), 7)
    '''
    run(make(pytester, test)).assert_outcomes(passed=1)
    assert numbered(lines(pytester, "report.txt")) == [
        "1. Untitled   ✓",
        "   1.1. Repeat until done   4 times, 0 ms",
        "      1.1.1. dev.fifo.data = 0, 1, 2, 3",
        "      1.1.2. Verify dev.da == 7 -> got 7   ✓",
    ]
    (loop,) = entries(pytester)
    check = loop["entries"][1]
    assert [r["target"]["concrete"] for r in check["reads"]] == ["dev.da"]


def test_titled_repeat_check_with_a_register_read(pytester):
    test = '''
    from stepdoc import repeat

    def test_it(dev, verify):
        with repeat("Each"):
            for i in range(3):
                dev.fifo.data = i
                verify.equal(dev.fifo.flag, 0, name="Flag")
    '''
    run(make(pytester, test)).assert_outcomes(passed=1)
    assert numbered(lines(pytester, "report.txt")) == [
        "1. Each (repeat)   ✓ 3 times",
        "   1.1. dev.fifo.data = 0, 1, 2",
        "   1.2. Verify 'Flag': dev.fifo.flag == 0 -> got 0   ✓",
    ]
    check = entries(pytester)[1]
    assert len(check["reads"]) == 1


def test_a_failed_check_is_shown_for_every_pass(pytester):
    """Not folded: each pass's result follows the pass, in order; the loop keeps
    its count."""
    test = '''
    from stepdoc import repeat, step

    def test_titled(dev, verify):
        with repeat("Each"):
            for i in range(4):
                dev.fifo.data = i
                got = dev.da_read(0x10)
                verify.equal(got, 7 if i != 2 else 8, name="RB")
                verify.equal(got, 7, name="OK")

    def test_auto(dev, verify):
        with step("Auto"):
            for i in range(3):
                dev.fifo.data = i
                got = dev.da_read(0x10)
                verify.equal(got, 7 if i != 1 else 8, name="RB")
    '''
    run(make(pytester, test)).assert_outcomes(failed=2)
    report = numbered(lines(pytester, "report.txt"))
    assert report == [
        "1. Each (repeat)   ✗ 4 times",
        "   1.1. dev.fifo.data = 0, 1, 2, 3",
        "   1.2. Read dev.da -> 7",
        "   1.3. Verify 'OK': got == 7 -> got 7   ✓",
        "   1.4. Verify 'RB': got == 7 -> got 7   ✓",
        "   1.5. Verify 'RB': got == 7 -> got 7   ✓",
        "   1.6. Verify 'RB': got == 8 -> got 7   ✗",
        "   1.7. Verify 'RB': got == 7 -> got 7   ✓",
        "1. Auto   ✗",
        "   1.1. Repeat   3 times, 0 ms",
        "      1.1.1. dev.fifo.data = 0, 1, 2",
        "      1.1.2. Read dev.da -> 7",
        "   1.2. Verify 'RB': got == 7 -> got 7   ✓",
        "   1.3. Verify 'RB': got == 8 -> got 7   ✗",
        "   1.4. Verify 'RB': got == 7 -> got 7   ✓",
    ]


def test_checks_of_some_passes_and_outside_loops_stay(pytester):
    test = '''
    from stepdoc import repeat

    def test_it(dev, verify):
        verify.equal(1, 1, name="Before")
        with repeat("Each"):
            for i in range(4):
                dev.fifo.data = i
                if i % 2:
                    verify.equal(i % 2, 1, name="Odd")
    '''
    run(make(pytester, test)).assert_outcomes(passed=1)
    report = numbered(lines(pytester, "report.txt"))
    assert report == [
        "1. Each (repeat)   ✓ 4 times",
        "   1.1. dev.fifo.data = 0, 1, 2, 3",
        "   1.2. Verify 'Odd': i % 2 == 1 -> got 1   ✓",
        "   1.3. Verify 'Odd': i % 2 == 1 -> got 1   ✓",
    ]
    assert "   - Verify 'Before' == 1 -> got 1   ✓" in lines(pytester, "report.txt")


def test_unscoped_loop(pytester):
    test = '''
    def test_it(dev, verify):
        for i in range(3):
            dev.fifo.data = i
            got = dev.da_read(0x10)
            verify.equal(got, 7, name="RB")
    '''
    run(make(pytester, test)).assert_outcomes(passed=1)
    assert [line.strip() for line in lines(pytester, "report.txt")][-4:] == [
        "- Repeat   3 times, 0 ms",
        "- dev.fifo.data = 0, 1, 2",
        "- Read dev.da -> 7",
        "- Verify 'RB': got == 7 -> got 7   ✓",
    ]


def test_dry_run_folds_unjudged_checks(pytester):
    run(make(pytester, TITLED), "--stepdoc-dry-run").assert_outcomes(passed=1)
    (loop,) = entries(pytester)
    assert [e["type"] for e in loop["entries"]] == ["action", "action", "check"]
    assert loop["entries"][2]["passed"] is None


def test_nothing_folds_without_collapsing(pytester):
    run(make(pytester, TITLED, "stepdoc_collapse_repeats = false")).assert_outcomes(passed=1)
    checks = [line for line in lines(pytester, "report.txt") if "Verify 'Readback'" in line]
    assert len(checks) == 6


def test_loop_at(tmp_path):
    src = tmp_path / "m.py"
    src.write_text(
        "def f(xs):\n"
        "    for x in xs:\n"
        "        while x:\n"
        "            x -= 1\n"
        "        print(x)\n"
        "    return 1\n"
    )
    assert loop_at(str(src), 4) == (3, 4)
    assert loop_at(str(src), 5) == (2, 5)
    assert loop_at(str(src), 6) is None
    assert loop_at(str(tmp_path / "missing.py"), 1) is None


def test_titled_repeat_folds_only_checks_made_once_per_pass(pytester):
    """Totals that match the count by chance do not fold: a check in an inner
    loop of some passes, or in a second loop after the one the actions ran in."""
    test = '''
    from stepdoc import repeat

    def test_some_passes(dev, verify):
        with repeat("Each"):
            for i in range(4):
                dev.fifo.data = i
                if i < 2:
                    for j in range(2):
                        verify.equal(j, j, name="X")

    def test_second_loop(dev, verify):
        with repeat("T"):
            for i in range(3):
                dev.fifo.data = i
            for k in range(3):
                verify.equal(k, k, name="K")
    '''
    run(make(pytester, test)).assert_outcomes(passed=2)
    report = numbered(lines(pytester, "report.txt"))
    assert report == [
        "1. Each (repeat)   ✓ 4 times",
        "   1.1. dev.fifo.data = 0, 1, 2, 3",
        "   1.2. Verify 'X': j == 0 -> got 0   ✓",
        "   1.3. Verify 'X': j == 1 -> got 1   ✓",
        "   1.4. Verify 'X': j == 0 -> got 0   ✓",
        "   1.5. Verify 'X': j == 1 -> got 1   ✓",
        "1. T (repeat)   ✓ 3 times",
        "   1.1. dev.fifo.data = 0, 1, 2",
        "   1.2. Verify 'K': k == 0 -> got 0   ✓",
        "   1.3. Verify 'K': k == 1 -> got 1   ✓",
        "   1.4. Verify 'K': k == 2 -> got 2   ✓",
    ]


CHECK_FIRST = '''
from stepdoc import repeat, step

def test_auto(dev, verify):
    with step("Auto"):
        for i in range(4):
            verify.equal(i, i, name="First")
            dev.fifo.data = i

def test_untitled(dev, verify):
    with step("Untitled"):
        with repeat(until="done"):
            for i in range(4):
                verify.equal(i, i, name="First")
                dev.fifo.data = i

def test_titled(dev, verify):
    with repeat("Titled"):
        for i in range(4):
            verify.equal(i, i, name="First")
            dev.fifo.data = i
'''


def test_a_check_before_the_first_action_of_the_pass_folds(pytester):
    run(make(pytester, CHECK_FIRST)).assert_outcomes(passed=3)
    assert numbered(lines(pytester, "report.txt")) == [
        "1. Auto   ✓",
        "   1.1. Repeat   4 times, 0 ms",
        "      1.1.1. Verify 'First': i == 3 -> got 3   ✓",
        "      1.1.2. dev.fifo.data = 0, 1, 2, 3",
        "1. Untitled   ✓",
        "   1.1. Repeat until done   4 times, 0 ms",
        "      1.1.1. Verify 'First': i == 3 -> got 3   ✓",
        "      1.1.2. dev.fifo.data = 0, 1, 2, 3",
        "1. Titled (repeat)   ✓ 4 times",
        "   1.1. Verify 'First': i == 3 -> got 3   ✓",
        "   1.2. dev.fifo.data = 0, 1, 2, 3",
    ]


def test_a_failed_check_before_the_first_action_stays_visible(pytester):
    test = CHECK_FIRST.replace("verify.equal(i, i,", "verify.equal(i, i if i != 2 else 9,")
    run(make(pytester, test)).assert_outcomes(failed=3)
    first = [
        "   1.1. Verify 'First': i == 0 -> got 0   ✓",
        "   1.2. Verify 'First': i == 1 -> got 1   ✓",
        "   1.3. Verify 'First': i == 9 -> got 2   ✗",
        "   1.4. Verify 'First': i == 3 -> got 3   ✓",
    ]
    assert numbered(lines(pytester, "report.txt")) == [
        "1. Auto   ✗",
        *first,
        "   1.5. Repeat dev.fifo.data = 0, 1, 2, 3 -> (4 times, 0 ms)",  # the count stays
        "1. Untitled   ✗",
        *first,
        "   1.5. Repeat until done   4 times, 0 ms",
        "      1.5.1. dev.fifo.data = 0, 1, 2, 3",
        "1. Titled (repeat)   ✗ 4 times",
        "   1.1. dev.fifo.data = 0, 1, 2, 3",
        "   1.2. Verify 'First': i == 0 -> got 0   ✓",
        "   1.3. Verify 'First': i == 1 -> got 1   ✓",
        "   1.4. Verify 'First': i == 9 -> got 2   ✗",
        "   1.5. Verify 'First': i == 3 -> got 3   ✓",
    ]


CHANNELS = '''
from stepdoc import repeat, step

def test_auto(dev, verify):
    with step("Channels"):
        for i in range(12):
            dat = getattr(dev, f"ch{i % 4}")
            verify.equal(dat.txfe, 1)

def test_titled(dev, verify):
    with repeat("Every channel"):
        for i in range(12):
            dat = getattr(dev, f"ch{i % 4}")
            verify.equal(dat.txfe, 1)
'''

CHANNEL_DEV = '''

@pytest.fixture
def dev():
    d = Dev()
    for ch in range(4):
        setattr(d, f"ch{ch}", Reg(f"dev.ch{ch}", {"txfe": 1}))
    return d
'''


def test_a_pass_of_several_iterations_folds_per_position(pytester):
    """A loop over channels (``i % 4``) collapses into passes of 4 iterations:
    one check per position in the pass, as asserts give."""
    make(pytester, CHANNELS)
    pytester.makeconftest(CONFTEST + CHANNEL_DEV)
    run(pytester).assert_outcomes(passed=2)
    report = numbered(lines(pytester, "report.txt"))
    assert report == [
        "1. Channels   ✓",
        "   1.1. Repeat   3 times, 0 ms",
        "      1.1.1. Verify dev.ch0.txfe == 1 -> got 1   ✓",
        "      1.1.2. Verify dev.ch1.txfe == 1 -> got 1   ✓",
        "      1.1.3. Verify dev.ch2.txfe == 1 -> got 1   ✓",
        "      1.1.4. Verify dev.ch3.txfe == 1 -> got 1   ✓",
        "1. Every channel (repeat)   ✓ 3 times",
        "   1.1. Verify dev.ch0.txfe == 1 -> got 1   ✓",
        "   1.2. Verify dev.ch1.txfe == 1 -> got 1   ✓",
        "   1.3. Verify dev.ch2.txfe == 1 -> got 1   ✓",
        "   1.4. Verify dev.ch3.txfe == 1 -> got 1   ✓",
    ]


RETRY_CONFTEST = '''

from _pytest.runner import runtestprotocol


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    """Two attempts on one recorder, as pytest-reporter's retries run them."""
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    runtestprotocol(item, nextitem=None, log=False)
    for report in runtestprotocol(item, nextitem=nextitem, log=False):
        item.ihook.pytest_runtest_logreport(report=report)
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True
'''


def test_a_retry_folds_its_own_loop(pytester):
    """Each attempt's checks go to its own steps (``Recorder.attempt``)."""
    make(pytester, TITLED)
    pytester.makeconftest(CONFTEST + RETRY_CONFTEST)
    run(pytester).assert_outcomes(passed=1)
    record = json.loads((pytester.path / "run.json").read_text())
    steps = record["tests"][0]["steps"]
    assert len(steps) == 2
    for s in steps:
        (loop,) = s["entries"]
        assert [e["type"] for e in loop["entries"]] == ["action", "action", "check"]


def test_a_loop_of_one_write_becomes_a_repeat_with_its_check(pytester):
    """A pass of one action collapses into the action (``4 times``); with the
    pass's check it is a Repeat, as live asserts make it."""
    test = '''
    from stepdoc import step

    def test_it(dev, verify):
        with step("Auto"):
            for i in range(4):
                dev.fifo.data = i
                verify.equal(i, i, name="I")
    '''
    run(make(pytester, test)).assert_outcomes(passed=1)
    assert numbered(lines(pytester, "report.txt")) == [
        "1. Auto   ✓",
        "   1.1. Repeat   4 times, 0 ms",
        "      1.1.1. dev.fifo.data = 0, 1, 2, 3",
        "      1.1.2. Verify 'I': i == 3 -> got 3   ✓",
    ]


def test_titled_repeat_whose_actions_are_made_in_a_helper(pytester):
    """No entry of the pass was made in a loop (the writes come from a helper):
    the loop is the one ``for`` statement of the ``with repeat(...)`` block. With
    two loops there, nothing tells which one the pass is, and nothing folds."""
    test = '''
    from stepdoc import repeat

    def put(dev, value):
        dev.fifo.data = value

    def test_one_loop(dev, verify):
        with repeat("Each"):
            for i in range(3):
                put(dev, i)
                verify.equal(i, i, name="I")

    def test_two_loops(dev, verify):
        with repeat("Each"):
            for i in range(3):
                put(dev, i)
            for k in range(3):
                verify.equal(k, k, name="K")
    '''
    run(make(pytester, test)).assert_outcomes(passed=2)
    report = numbered(lines(pytester, "report.txt"))
    assert report[:3] == [
        "1. Each (repeat)   ✓ 3 times",
        "   1.1. dev.fifo.data = 0, 1, 2",
        "   1.2. Verify 'I': i == 2 -> got 2   ✓",
    ]
    assert report[3:] == [
        "1. Each (repeat)   ✓ 3 times",
        "   1.1. dev.fifo.data = 0, 1, 2",
        "   1.2. Verify 'K': k == 0 -> got 0   ✓",
        "   1.3. Verify 'K': k == 1 -> got 1   ✓",
        "   1.4. Verify 'K': k == 2 -> got 2   ✓",
    ]
