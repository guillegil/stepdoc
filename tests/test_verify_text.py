"""Verify lines that read like the code (CHK-9, CHK-10).

A check's text starts with the checked value: the register a read on the same
line produced (``dev.block.esmi<channel>_32dat.txfe == 1``), else the argument's
source; a ``note=`` or a source comment is shown after it in parentheses.
pytest-verifier is not installed here: a fake publishes descriptors shaped like
its own, with ``auto_name``, ``note`` and the operand names (``actual_name``,
``expected_name`` and their ``_symbolic`` forms). tests/test_verify_operands.py
runs the real plugin when it is installed."""

import json
import re
from pathlib import Path

import jsonschema
import pytest

from stepdoc import Check, Location, Recorder, step
from stepdoc.adapters._source import calls_at
from stepdoc.adapters.verify import to_check
from stepdoc.core.comments import clear_caches, comment_note
from stepdoc.core.record import case_record, run_record
from stepdoc.renderers import render
from tests.test_adapters import FAKE_LOADER, SCHEMA, VERIFY_SPECS

pytest_plugins = ["pytester"]

# A fake pytest-verifier whose name= is optional: without one, the name is the
# source of the first argument and the descriptor says ``auto_name``. Operand
# names are stored as pytest-verifier 0.11 stores them; the symbolic form comes
# from an f-string written on the call's line.
FAKE_VERIFY = '''
import linecache
import os
import re
import sys

import pytest

_key = pytest.StashKey()


def pytest_addhooks(pluginmanager):
    import verify_specs

    pluginmanager.add_hookspecs(verify_specs)


class Verify:
    def __init__(self, item):
        self.item = item
        self.records = []
        self.calls = {}

    def equal(self, actual, expected, *, name=None, note=None, actual_name=None, expected_name=None):
        shown = repr(expected) if expected_name is None else f"{expected_name} ({expected!r})"
        return self._add("equal", name, "== " + shown, actual == expected, note,
                         actual_name, expected_name, actual=actual, expected=expected)

    def between(self, actual, low, high, *, name=None, note=None, actual_name=None):
        return self._add("between", name, f"∈ [{low!r}, {high!r}]", low <= actual <= high, note,
                         actual_name, actual=actual, low=low, high=high)

    def is_true(self, actual, *, name=None, note=None, actual_name=None):
        return self._add("true", name, "is True", bool(actual), note, actual_name, actual=actual)

    def _add(self, kind, name, rest, passed, note, actual_name=None, expected_name=None, **fields):
        frame = sys._getframe(2)
        root = str(self.item.config.rootpath)
        d = {"check_type": kind, "phase": "call", "passed": passed,
             "location": f"{os.path.relpath(frame.f_code.co_filename, root)}:{frame.f_lineno}", **fields}
        source = linecache.getline(frame.f_code.co_filename, frame.f_lineno)
        for key, text in (("actual_name", actual_name), ("expected_name", expected_name)):
            if text is None:
                continue
            d[key] = text
            written = re.search(key + r'=f"([^"]*)"', source)
            symbolic = re.sub(r"\\{([^{}]+)\\}", r"<\\1>", written.group(1)) if written else text
            if symbolic != text:
                d[key + "_symbolic"] = symbolic
        if name is None and actual_name is not None:
            name = actual_name
            d["auto_name"] = True
        caller = frame
        while caller is not None and not caller.f_code.co_name.startswith("test_"):
            caller = caller.f_back
        if caller is not None and caller is not frame:
            d["called_from"] = f"{os.path.relpath(caller.f_code.co_filename, root)}:{caller.f_lineno}"
        if name is None:
            # The source of the first argument; with several calls on the line,
            # the next one each time (calls on one line run left to right).
            line = linecache.getline(frame.f_code.co_filename, frame.f_lineno)
            method = {"true": "is_true"}.get(kind, kind)
            found = re.findall(r"\\." + method + r"\\((.+?)[,)]", line)
            seen = self.calls.setdefault((frame.f_code, frame.f_lineno), [0])
            name = found[seen[0] % len(found)].strip() if found else "value"
            seen[0] += 1
            d["auto_name"] = True
        if note is not None and note.strip():
            d["note"] = note.strip()
        d["name"] = name
        d["description"] = f"Verify '{name}' {rest}"
        self.records.append(d)
        return d


@pytest.fixture
def verify(request):
    v = Verify(request.node)
    request.node.stash[_key] = v
    return v


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    result = yield
    v = item.stash.get(_key, None)
    if v is not None and v.records:
        passed = all(r["passed"] for r in v.records)
        item.ihook.pytest_verify_results(item=item, when="call", checks=list(v.records), passed=passed)
        if not passed:
            raise AssertionError("soft checks failed")
    return result
'''

# A register map reached as ``tb.dev.block.esmi<n>_<reg>.<field>``; every field
# read is recorded by the bridge in conftest.py.
FAKE_DEVICE = '''
class Reg:
    def __init__(self, dev, path, fields):
        object.__setattr__(self, "_dev", dev)
        object.__setattr__(self, "_path", path)
        object.__setattr__(self, "_fields", dict(fields))

    def __getattr__(self, name):
        if name not in self._fields:
            raise AttributeError(name)
        value = self._fields[name]
        for hook in self._dev.on_read:
            hook(f"{self._path}.{name}", value)
        return value


class Block:
    def __init__(self, dev, channels):
        for ch in channels:
            setattr(self, f"esmi{ch}_ctl", Reg(dev, f"dev.block.esmi{ch}_ctl", {"tx": 0, "rx": 1}))
            setattr(self, f"esmi{ch}_32dat", Reg(dev, f"dev.block.esmi{ch}_32dat", {"txfe": 1, "tx8e": 1}))


class Dev:
    def __init__(self, channels=(0, 1, 2, 3)):
        self.on_read = []
        self.block = Block(self, channels)


class Testbench:
    def __init__(self):
        self.dev = Dev()
'''

CONFTEST = '''
import pytest

import stepdoc
from fakedev import Testbench


@pytest.fixture
def tb():
    t = Testbench()
    t.dev.on_read.append(lambda path, value: stepdoc.record_action(path, "read", value))
    return t
'''

EXAMPLE = '''
import pytest
from stepdoc import step


@pytest.mark.parametrize("channel", [3])
def test_fifo(verify, tb, channel):
    ctl = getattr(tb.dev.block, f"esmi{channel}_ctl")
    dat = getattr(tb.dev.block, f"esmi{channel}_32dat")
    with step("Verify CMD FIFO flags at startup"):
        # CMD FIFO empty
        verify.equal(dat.txfe, 1)
        verify.equal(dat.tx8e, 1, note="at least 8 empty positions")
        verify.equal(ctl.tx, 0, name="FIFO not full")  # room for commands
'''


@pytest.fixture
def project(pytester):
    pytester.makepyfile(
        load_fake_verifier=FAKE_LOADER, fake_verifier=FAKE_VERIFY, verify_specs=VERIFY_SPECS,
        fakedev=FAKE_DEVICE,
    )
    pytester.makeconftest(CONFTEST)
    pytester.makeini("[pytest]\nstepdoc_skip_modules = fakedev\nenable_assertion_pass_hook = true\n")
    return pytester


def run(pytester, *args):
    out = ("--stepdoc-record=run.json", "--stepdoc-procedure=procedure.txt", "--stepdoc-report=report.txt")
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "load_fake_verifier", *out, *args)


def lines(pytester, name):
    return [line.rstrip() for line in (pytester.path / name).read_text().splitlines()]


def numbered(text_lines):
    return [line for line in text_lines if re.match(r"\s*\d", line)]


def test_the_users_example(project):
    project.makepyfile(test_fifo=EXAMPLE)
    run(project).assert_outcomes(passed=1)
    assert numbered(lines(project, "procedure.txt")) == [
        "1. Verify CMD FIFO flags at startup",
        "   1.1. Verify dev.block.esmi<channel>_32dat.txfe == 1 (CMD FIFO empty)",
        "   1.2. Verify dev.block.esmi<channel>_32dat.tx8e == 1 (at least 8 empty positions)",
        "   1.3. Verify 'FIFO not full': dev.block.esmi<channel>_ctl.tx == 0 (room for commands)",
    ]
    report = lines(project, "report.txt")
    assert "   1.1. Verify dev.block.esmi3_32dat.txfe == 1 (CMD FIFO empty) -> got 1   ✓" in report
    assert "   1.2. Verify dev.block.esmi3_32dat.tx8e == 1 (at least 8 empty positions) -> got 1   ✓" in report
    assert "   1.3. Verify 'FIFO not full': dev.block.esmi3_ctl.tx == 0 (room for commands) -> got 0   ✓" in report
    record = json.loads((project.path / "run.json").read_text())
    jsonschema.validate(record, SCHEMA)
    (s,) = record["tests"][0]["steps"]
    assert [e["type"] for e in s["entries"]] == ["check"] * 3  # the reads went into the checks
    first = s["entries"][0]
    assert first["text"] == "dev.block.esmi3_32dat.txfe == 1"
    assert first["symbolic_text"] == "dev.block.esmi<channel>_32dat.txfe == 1"
    assert first["note"] == "CMD FIFO empty"
    assert [r["target"]["concrete"] for r in first["reads"]] == ["dev.block.esmi3_32dat.txfe"]
    assert first["reads"][0]["number"] is None


def test_markdown_and_html_show_the_note_as_prose(project):
    project.makepyfile(
        test_md='''
        from stepdoc import step

        def test_it(verify, tb):
            dat = tb.dev.block.esmi0_32dat
            with step("Flags"):
                verify.equal(dat.txfe, 1, note="<b>empty</b> *now*")
        '''
    )
    project.runpytest_subprocess(
        "-p", "no:cacheprovider", "-p", "load_fake_verifier",
        "--stepdoc-procedure=p.md", "--stepdoc-report=r.html",
    ).assert_outcomes(passed=1)
    md = (project.path / "p.md").read_text()
    assert r"1.1. Verify `dev.block.esmi0_32dat.txfe == 1` (\<b\>empty\</b\> \*now\*)" in md
    html = (project.path / "r.html").read_text()
    assert '<span class="aside">(&lt;b&gt;empty&lt;/b&gt; *now*)</span>' in html
    assert "<b>empty" not in html


def test_loops_reads_and_values_without_reads(project):
    project.makepyfile(
        test_loops='''
        from stepdoc import step

        def test_it(verify, tb):
            write_count = 0
            with step("Poll"):
                for _ in range(3):
                    verify.equal(tb.dev.block.esmi1_ctl.rx, 1)
            with step("Every channel"):
                for ch in range(2):
                    reg = getattr(tb.dev.block, f"esmi{ch}_ctl")
                    verify.equal(reg.tx, 0, name="Idle")
            for ch in (2, 3):
                with step("Channel"):
                    dat = getattr(tb.dev.block, f"esmi{ch}_32dat")
                    verify.between(dat.tx8e, 0, 1)
            with step("Count"):
                write_count += 500
                verify.equal(write_count, 500)
                verify.is_true(write_count > 1, note="")
                verify.equal(201, 201, name="HTTP status")
        '''
    )
    run(project).assert_outcomes(passed=1)
    assert numbered(lines(project, "procedure.txt")) == [
        "1. Poll",
        "   1.1. Verify dev.block.esmi1_ctl.rx == 1",
        "   1.2. Verify dev.block.esmi1_ctl.rx == 1",
        "   1.3. Verify dev.block.esmi1_ctl.rx == 1",
        "2. Every channel",
        "   2.1. Verify 'Idle': dev.block.esmi<ch>_ctl.tx == 0",
        "   2.2. Verify 'Idle': dev.block.esmi<ch>_ctl.tx == 0",
        "3. Channel",
        "   3.1. Verify dev.block.esmi<ch>_32dat.tx8e ∈ [0, 1]",
        "4. Channel",
        "   4.1. Verify dev.block.esmi<ch>_32dat.tx8e ∈ [0, 1]",
        "5. Count",
        "   5.1. Verify write_count == 500",
        "   5.2. Verify write_count > 1 is True",
        "   5.3. Verify 'HTTP status' == 201",
    ]
    report = lines(project, "report.txt")
    assert "   2.1. Verify 'Idle': dev.block.esmi0_ctl.tx == 0 -> got 0   ✓" in report
    assert "   2.2. Verify 'Idle': dev.block.esmi1_ctl.tx == 0 -> got 0   ✓" in report
    assert "   4.1. Verify dev.block.esmi3_32dat.tx8e ∈ [0, 1] -> got 1   ✓" in report
    assert "   5.1. Verify write_count == 500 -> got 500   ✓" in report
    record = json.loads((project.path / "run.json").read_text())
    poll = record["tests"][0]["steps"][0]["entries"]
    assert len(poll[0]["reads"]) == 1 and poll[0]["reads"][0]["count"] == 3  # polled once, folded once
    assert [len(e["reads"]) for e in poll[1:]] == [0, 0]
    assert "note" not in record["tests"][0]["steps"][4]["entries"][1]  # a blank note= is no note


def test_each_pass_names_its_own_register(project):
    """A check never takes a register read made for another pass (a polled read
    feeds as many checks as it stands for; a helper's reads go to its calls)."""
    project.makepyfile(
        test_passes='''
        from stepdoc import step


        def check_ready(verify, reg):
            verify.equal(reg.rx, 1)


        def test_it(verify, tb):
            with step("Poll every channel"):
                for ch in range(2):
                    for _ in range(3):
                        verify.equal(getattr(tb.dev.block, f"esmi{ch}_ctl").rx, 1)
            with step("Helper"):
                check_ready(verify, tb.dev.block.esmi0_ctl)
                check_ready(verify, tb.dev.block.esmi1_ctl)
        '''
    )
    run(project).assert_outcomes(passed=1)
    report = [line for line in numbered(lines(project, "report.txt")) if "Verify" in line or "Read" in line]
    assert report == [
        *[f"   1.{i}. Verify dev.block.esmi0_ctl.rx == 1 -> got 1   ✓" for i in (1, 2, 3)],
        *[f"   1.{i}. Verify dev.block.esmi1_ctl.rx == 1 -> got 1   ✓" for i in (4, 5, 6)],
        "   2.1. Verify dev.block.esmi0_ctl.rx == 1 -> got 1   ✓",
        "   2.2. Verify dev.block.esmi1_ctl.rx == 1 -> got 1   ✓",
    ]
    procedure = numbered(lines(project, "procedure.txt"))
    assert "   1.1. Verify dev.block.esmi<ch>_ctl.rx == 1" in procedure


def test_aliases_read_the_same_in_both_documents(project):
    project.makepyfile(
        test_alias='''
        from stepdoc import step


        class R:
            items = [1, 1]


        def test_it(verify, tb):
            with step("Loop"):
                for ch in range(2):
                    v = getattr(tb.dev.block, f"esmi{ch}_32dat").txfe
                    verify.equal(v, 1)
            with step("Once"):
                w = tb.dev.block.esmi3_32dat.tx8e
                verify.equal(w, 1)
            with step("Items"):
                channel = 3
                resp = R()
                verify.equal(resp.items[channel - 3], 1)
        '''
    )
    run(project).assert_outcomes(passed=1)
    procedure = numbered(lines(project, "procedure.txt"))
    report = numbered(lines(project, "report.txt"))
    # Each pass binds ``v`` to another register: the check reads as written.
    assert [line for line in procedure if "Verify" in line][:2] == [
        "   1.3. Verify v == 1", "   1.4. Verify v == 1",
    ]
    assert "   1.3. Verify v == 1 -> got 1   ✓" in report
    # One read bound to the variable: the check names that register.
    assert "   2.2. Verify dev.block.esmi3_32dat.tx8e == 1" in procedure
    assert "   2.2. Verify dev.block.esmi3_32dat.tx8e == 1 -> got 1   ✓" in report
    assert "   3.1. Verify resp.items[<channel - 3>] == 1" in procedure  # no R() from the alias
    assert "   3.1. Verify resp.items[channel - 3] == 1 -> got 1   ✓" in report
    assert not [line for line in procedure if "tb." in line or "R()" in line]


def test_two_checks_on_one_line(project):
    project.makepyfile(
        test_one_line='''
        from stepdoc import step


        def test_it(verify, tb):
            dat = tb.dev.block.esmi0_32dat
            with step("Both"):
                verify.equal(dat.txfe, 1); verify.equal(dat.tx8e, 1)
            with step("Named"):
                verify.equal(dat.txfe, 1, name="A"); verify.equal(dat.tx8e, 1, name="B")
        '''
    )
    run(project).assert_outcomes(passed=1)
    procedure = numbered(lines(project, "procedure.txt"))
    assert procedure[1:3] == [
        "   1.1. Verify dev.block.esmi0_32dat.txfe == 1",
        "   1.2. Verify dev.block.esmi0_32dat.tx8e == 1",
    ]
    # Named checks cannot be told apart by their source: each keeps its plain text.
    assert "   2.3. Verify 'A' == 1" in procedure and "   2.4. Verify 'B' == 1" in procedure
    record = json.loads((project.path / "run.json").read_text())
    both = record["tests"][0]["steps"][0]["entries"]
    assert [[r["target"]["concrete"] for r in e["reads"]] for e in both] == [
        ["dev.block.esmi0_32dat.txfe"], ["dev.block.esmi0_32dat.tx8e"],
    ]


def test_two_checks_on_one_line_are_told_apart_by_their_operand_names(project):
    project.makepyfile(
        test_it='''
        from stepdoc import step


        def test_it(verify, tb):
            w = 0
            with step("Two names"):
                verify.equal(tb.dev.block.esmi1_ctl.tx, w, actual_name="R1"); verify.equal(tb.dev.block.esmi2_ctl.tx, w, actual_name="R2", name="Second")
            with step("One name twice"):
                verify.equal(tb.dev.block.esmi1_ctl.tx, w, actual_name="R"); verify.equal(tb.dev.block.esmi2_ctl.tx, w, actual_name="R")
        '''
    )
    run(project).assert_outcomes(passed=1)
    assert numbered(lines(project, "procedure.txt")) == [
        "1. Two names",
        "   1.1. Verify R1 == 0",
        "   1.2. Verify 'Second': R2 == 0",
        "2. One name twice",
        # Either call may have made each check: plain text, reads on their own lines.
        "   2.1. Read dev.block.esmi1_ctl.tx",
        "   2.2. Read dev.block.esmi2_ctl.tx",
        "   2.3. Verify R == 0",
        "   2.4. Verify R == 0",
    ]
    # Each check took the read of its own call.
    record = json.loads((project.path / "run.json").read_text())
    assert [[r["target"]["concrete"] for r in e["reads"]] for e in record["tests"][0]["steps"][0]["entries"]] == [
        ["dev.block.esmi1_ctl.tx"], ["dev.block.esmi2_ctl.tx"],
    ]


def test_calls_at_takes_outer_calls(tmp_path):
    path = tmp_path / "calls.py"
    path.write_text("verify.equal(a.equal(b), True)\nverify.equal(x, 1); verify.equal(y, 2)\n")
    (outer,) = calls_at(str(path), 1, "equal")
    assert ast_text(outer.args[0]) == "a.equal(b)"
    assert [ast_text(c.args[0]) for c in calls_at(str(path), 2, "equal")] == ["x", "y"]


def ast_text(node):
    import ast

    return ast.unparse(node)


COMMENTS = '''
from stepdoc import step


def test_it(verify, tb):
    dat = tb.dev.block.esmi0_32dat
    with step("Notes"):
        # The FIFO is empty
        # after reset.
        verify.equal(dat.txfe, 1)
        verify.equal(dat.tx8e, 1)  # TODO: check 16 too
        # FIFO not full

        verify.equal(dat.txfe, 1)
        # verify.equal(dat.tx8e, 0)
        verify.equal(dat.tx8e, 1, note="given")  # trailing
        verify.equal(  # split
            dat.txfe,
            1,
        )
        assert dat.tx8e == 1  # asserts too
        assert dat.txfe == 1, "# not a comment"
'''


def test_comment_notes(project):
    project.makepyfile(test_comments=COMMENTS)
    run(project).assert_outcomes(passed=1)
    assert numbered(lines(project, "procedure.txt")) == [
        "1. Notes",
        "   1.1. Verify dev.block.esmi0_32dat.txfe == 1 (The FIFO is empty after reset.)",
        "   1.2. Verify dev.block.esmi0_32dat.tx8e == 1",
        "   1.3. Verify dev.block.esmi0_32dat.txfe == 1",
        "   1.4. Verify dev.block.esmi0_32dat.tx8e == 1 (given)",
        "   1.5. Verify dev.block.esmi0_32dat.txfe == 1 (split)",
        "   1.6. Verify dat.tx8e == 1 (asserts too)",
        "   1.7. Verify dat.txfe == 1",
    ]


def test_comment_notes_are_there_when_the_test_ends(project):
    """Tools that read the steps at pytest_runtest_logfinish (pytest-reporter's
    procedure.json) see notes taken from comments, for verify checks and
    asserts."""
    project.makeconftest(
        CONFTEST
        + '''
import json

_steps = []


def pytest_stepdoc_step_finished(item, step):
    _steps.append(step)


def pytest_runtest_logfinish(nodeid, location):
    notes = [[c.text, c.note] for s in _steps for c in s.checks]
    with open("notes.json", "w") as f:
        json.dump(notes, f)
'''
    )
    project.makepyfile(
        test_notes='''
        from stepdoc import step


        def test_it(verify, tb):
            x = 1
            with step("S"):
                # comment note here
                verify.equal(x, 1, name="X")
                # assert note
                assert x == 1
        '''
    )
    run(project).assert_outcomes(passed=1)
    notes = json.loads((project.path / "notes.json").read_text())
    assert ["'X': x == 1", "comment note here"] in notes
    assert ["x == 1", "assert note"] in notes
    record = json.loads((project.path / "run.json").read_text())
    entries = record["tests"][0]["steps"][0]["entries"]
    assert [e.get("note") for e in entries] == ["comment note here", "assert note"]


def test_comment_notes_off(project):
    project.makepyfile(test_comments=COMMENTS)
    project.makeini(
        "[pytest]\nstepdoc_skip_modules = fakedev\nenable_assertion_pass_hook = true\n"
        "stepdoc_comment_notes = false\n"
    )
    run(project).assert_outcomes(passed=1)
    procedure = lines(project, "procedure.txt")
    assert "   1.1. Verify dev.block.esmi0_32dat.txfe == 1" in procedure
    assert "   1.4. Verify dev.block.esmi0_32dat.tx8e == 1 (given)" in procedure  # note= still shows
    assert not [line for line in procedure if "(asserts too)" in line or "(split)" in line]


# --------------------------------------------------------------------------- #
# The adapter on its own: descriptors of older pytest-verifier versions
# --------------------------------------------------------------------------- #


def test_old_descriptors_and_missing_source(tmp_path):
    source = tmp_path / "test_x.py"
    source.write_text("def test_x(verify):\n    verify.equal(201, 201, name='HTTP status')\n")
    old = {"check_type": "equal", "name": "HTTP status", "description": "Verify 'HTTP status' == 201",
           "passed": True, "actual": 201, "expected": 201, "location": "test_x.py:2"}
    check = to_check(old, str(tmp_path))
    assert (check.text, check.symbolic_text, check.note) == ("'HTTP status' == 201", None, None)
    # No source (exec, a REPL): today's text, also with an automatic name.
    gone = {**old, "name": "value", "description": "Verify 'value' == 201", "auto_name": True,
            "location": "missing.py:9", "note": "  why  "}
    check = to_check(gone, str(tmp_path))
    assert (check.text, check.symbolic_text, check.note) == ("'value' == 201", None, "why")
    # A description of another shape stays as it is.
    odd = {**old, "description": "something else", "location": "test_x.py:2"}
    assert to_check(odd, str(tmp_path)).text == "something else"


def test_children_keep_their_notes(tmp_path):
    d = {"check_type": "all_satisfy", "name": "All", "description": "Verify all items in 'All' satisfy condition",
         "passed": True, "child_checks": [{"check_type": "equal", "name": "x", "description": "Verify 'x' == 1",
                                           "passed": True, "note": "first"}]}
    check = to_check(d, str(tmp_path))
    # One check per item reads as one line (CHK-11), which takes the items' note.
    assert check.children == [] and check.note == "first"
    assert check.text == "'All': for each item, 'x' == 1"
    other = {"check_type": "greater", "name": "y", "description": "Verify 'y' > 0", "passed": True, "note": "second"}
    mixed = to_check({**d, "child_checks": [d["child_checks"][0], other]}, str(tmp_path))
    assert [c.note for c in mixed.children] == ["first", "second"] and mixed.note is None


# --------------------------------------------------------------------------- #
# Operand names: actual_name= and expected_name= word the check
# --------------------------------------------------------------------------- #

OPERANDS = '''
import pytest
from stepdoc import step


@pytest.mark.parametrize("channel", [3])
def test_readback(verify, tb, channel):
    wdata = 1
    with step("Read back"):
        f = getattr(tb.dev.block, f"esmi{channel}_32dat").txfe
        verify.equal(f, wdata, actual_name=f"tb.dev.map.block.reg{channel}.F", expected_name="wdata")
        verify.equal(f, wdata, name="Stored word", actual_name=f"tb.dev.map.block.reg{channel}.F", expected_name="wdata")
        verify.equal(f, 1, actual_name=f"tb.dev.map.block.reg{channel}.F", note="a literal")
        verify.equal(getattr(tb.dev.block, f"esmi{channel}_ctl").tx, 0, actual_name="ctl.tx")  # read here
        verify.equal(f, wdata, expected_name=f"limits[{channel}]")
        verify.is_true(f, actual_name="flag <set>")
'''


def test_operand_names_word_the_check(project):
    project.makepyfile(test_operands=OPERANDS)
    run(project).assert_outcomes(passed=1)
    assert numbered(lines(project, "procedure.txt")) == [
        "1. Read back",
        "   1.1. Read dev.block.esmi<channel>_32dat.txfe -> <f>",
        "   1.2. Verify tb.dev.map.block.reg<channel>.F == <wdata>",
        "   1.3. Verify 'Stored word': tb.dev.map.block.reg<channel>.F == <wdata>",
        "   1.4. Verify tb.dev.map.block.reg<channel>.F == 1 (a literal)",
        "   1.5. Verify ctl.tx == 0 (read here)",
        "   1.6. Verify dev.block.esmi<channel>_32dat.txfe == limits[<channel>]",  # named after its read
        "   1.7. Verify flag <set> is True",
    ]
    assert numbered(lines(project, "report.txt"))[1:] == [
        "   1.1. Read dev.block.esmi3_32dat.txfe -> 1",
        "   1.2. Verify tb.dev.map.block.reg3.F == wdata (1) -> got 1   ✓",
        "   1.3. Verify 'Stored word': tb.dev.map.block.reg3.F == wdata (1) -> got 1   ✓",
        "   1.4. Verify tb.dev.map.block.reg3.F == 1 (a literal) -> got 1   ✓",
        "   1.5. Verify ctl.tx == 0 (read here) -> got 0   ✓",
        "   1.6. Verify dev.block.esmi3_32dat.txfe == limits[3] (1) -> got 1   ✓",
        "   1.7. Verify flag <set> is True -> got 1   ✓",
    ]
    record = json.loads((project.path / "run.json").read_text())
    jsonschema.validate(record, SCHEMA)
    entries = record["tests"][0]["steps"][0]["entries"]
    first, named, _, folded, expected_only, _ = entries[1:]
    assert first["operands"] == {
        "actual": {"concrete": "tb.dev.map.block.reg3.F", "symbolic": "tb.dev.map.block.reg<channel>.F"},
        "expected": {"concrete": "wdata", "symbolic": "wdata"},
    }
    assert "label" not in first and named["label"] == "Stored word"
    # The read made on the check's line still goes into the check.
    assert [r["target"]["concrete"] for r in folded["reads"]] == ["dev.block.esmi3_ctl.tx"]
    assert expected_only["operands"] == {"expected": {"concrete": "limits[3]", "symbolic": "limits[<channel>]"}}

    # Markdown: the name given with name= is words, the rest one code span.
    md = render(record, "procedure", "md")
    assert "1.2. Verify `tb.dev.map.block.reg<channel>.F == <wdata>`" in md
    assert "1.3. Verify 'Stored word': `tb.dev.map.block.reg<channel>.F == <wdata>`" in md
    assert "reg3" not in md

    # HTML: <channel> shows as written, the code is one <code>, the label is not.
    html = render(record, "procedure", "html")
    code = '<code class="expr">tb.<wbr>dev.<wbr>map.<wbr>block.<wbr>reg&lt;channel&gt;.<wbr>F == &lt;wdata&gt;</code>'
    assert f"Verify {code}" in html
    assert f'Verify <span class="label">&#x27;Stored word&#x27;:</span> {code}' in html
    assert '<code class="expr">flag &lt;set&gt; is True</code>' in html
    assert "<channel>" not in html and "reg3" not in html
    css = html.split("</style>")[0]
    assert "--mono: ui-monospace," in css and "font-variant-ligatures: none" in css
    assert "http" not in css and "@import" not in css and "@font-face" not in css  # system fonts only
    # A long name= without spaces wraps like the code after it (narrow screens).
    assert ".body { flex: 1; min-width: 0; overflow-wrap: anywhere; }" in css
    report = render(record, "report", "html")
    assert '<code class="expr">tb.<wbr>dev.<wbr>map.<wbr>block.<wbr>reg3.<wbr>F == wdata (1)</code>' in report
    assert "reg&lt;channel&gt;" not in report


def test_operand_names_in_a_loop_and_across_cases(project):
    """Every pass and every case reads the same in the procedure: one variant."""
    project.makepyfile(
        test_loop='''
        import pytest
        from stepdoc import step


        @pytest.mark.parametrize("first", [0, 2])
        def test_channels(verify, tb, first):
            with step("Every channel"):
                for channel in (first, first + 1):
                    f = getattr(tb.dev.block, f"esmi{channel}_ctl").rx
                    verify.equal(f, 1, actual_name=f"block.reg{channel}.F")
            with step("Polled"):
                for _ in range(3):
                    verify.equal(tb.dev.block.esmi1_ctl.rx, 1, actual_name="block.reg1.F")
        '''
    )
    run(project).assert_outcomes(passed=2)
    procedure = lines(project, "procedure.txt")
    assert "Variant for" not in "\n".join(procedure)
    assert numbered(procedure) == [
        "1. Every channel",
        "   1.1. Read dev.block.esmi<channel>_ctl.rx -> <f>",
        "   1.2. Read dev.block.esmi<channel>_ctl.rx -> <f>",
        "   1.3. Verify block.reg<channel>.F == 1",
        "   1.4. Verify block.reg<channel>.F == 1",
        "2. Polled",
        *[f"   2.{i}. Verify block.reg1.F == 1" for i in (1, 2, 3)],
    ]
    report = numbered(lines(project, "report.txt"))
    for i, channel in ((3, 0), (4, 1), (3, 2), (4, 3)):  # each pass and case names its register
        assert f"   1.{i}. Verify block.reg{channel}.F == 1 -> got 1   ✓" in report
    record = json.loads((project.path / "run.json").read_text())
    polled = record["tests"][0]["steps"][1]["entries"]
    assert [len(e["reads"]) for e in polled] == [1, 0, 0] and polled[0]["reads"][0]["count"] == 3


def _named(**fields):
    d = {"check_type": "equal", "passed": True, "actual": 240, "expected": 243, "auto_name": True,
         "name": "dev.reg3.F", "actual_name": "dev.reg3.F", "actual_name_symbolic": "dev.reg<channel>.F",
         "description": "Verify 'dev.reg3.F' == wdata (243)", "expected_name": "wdata", **fields}
    return {k: v for k, v in d.items() if v is not None}


@pytest.mark.parametrize(
    "d, text, symbolic",
    [
        (_named(), "dev.reg3.F == wdata (243)", "dev.reg<channel>.F == <wdata>"),
        (_named(name="Stored word", auto_name=None, description="Verify 'Stored word' == wdata (243)"),
         "'Stored word': dev.reg3.F == wdata (243)", "'Stored word': dev.reg<channel>.F == <wdata>"),
        # A plain name is shown as given; no symbolic text when nothing differs.
        (_named(actual_name_symbolic=None, expected_name=None, description="Verify 'dev.reg3.F' == 243"),
         "dev.reg3.F == 243", None),
        (_named(expected_name_symbolic="limits[<channel>]", expected_name="limits[3]",
                description="Verify 'dev.reg3.F' == limits[3] (243)"),
         "dev.reg3.F == limits[3] (243)", "dev.reg<channel>.F == limits[<channel>]"),
        (_named(check_type="approx", units="V", expected_name="nominal",
                description="Verify 'dev.reg3.F' == nominal (3.3V) ± 0.05V (abs) ± 1% (rel)"),
         "dev.reg3.F == nominal (3.3V) ± 0.05V (abs) ± 1% (rel)",
         "dev.reg<channel>.F == <nominal> V ± 0.05V (abs) ± 1% (rel)"),
        (_named(check_type="greater_equal", units="dB", expected_name="floor",
                description="Verify 'dev.reg3.F' >= floor (-3dB)"),
         "dev.reg3.F >= floor (-3dB)", "dev.reg<channel>.F >= <floor> dB"),
        (_named(check_type="length", description="Verify 'dev.reg3.F' has length n (2)", expected_name="n"),
         "dev.reg3.F has length n (2)", "dev.reg<channel>.F has length <n>"),
        (_named(check_type="contains", expected_name=None, description="Verify 'dev.reg3.F' contains 'b'"),
         "dev.reg3.F contains 'b'", "dev.reg<channel>.F contains 'b'"),
        (_named(check_type="between", expected_name=None, description="Verify 'dev.reg3.F' ∈ [1A, 9A]"),
         "dev.reg3.F ∈ [1A, 9A]", "dev.reg<channel>.F ∈ [1A, 9A]"),
        (_named(check_type="is_instance", expected_name=None, description="Verify 'dev.reg3.F' is instance of int"),
         "dev.reg3.F is instance of int", "dev.reg<channel>.F is instance of int"),
        # Names with operators, quotes, parentheses and backticks stay as written.
        (_named(name="it's `a` == (b) < c", actual_name="it's `a` == (b) < c", actual_name_symbolic=None,
                check_type="less", expected_name="x (y) ± z == 1",
                description="Verify 'it's `a` == (b) < c' < x (y) ± z == 1 (30)"),
         "it's `a` == (b) < c < x (y) ± z == 1 (30)", "it's `a` == (b) < c < <x (y) ± z == 1>"),
        # ``<`` and ``>`` that compare are not a variable part: the name gets its ``<...>``.
        (_named(expected_name="a < b > c", description="Verify 'dev.reg3.F' == a < b > c (243)"),
         "dev.reg3.F == a < b > c (243)", "dev.reg<channel>.F == <a < b > c>"),
        (_named(expected_name="lim[<i + 1>]", description="Verify 'dev.reg3.F' == lim[<i + 1>] (243)"),
         "dev.reg3.F == lim[<i + 1>] (243)", "dev.reg<channel>.F == lim[<i + 1>]"),
        # Parts pytest-verifier found are parts, however they read.
        (_named(expected_name="lim 3", expected_name_symbolic="lim < channel >",
                description="Verify 'dev.reg3.F' == lim 3 (243)"),
         "dev.reg3.F == lim 3 (243)", "dev.reg<channel>.F == lim < channel >"),
        # An expected name alone: the checked value keeps the name of the check.
        (_named(name="Level", auto_name=None, actual_name=None, actual_name_symbolic=None,
                description="Verify 'Level' == wdata (243)"),
         "'Level' == wdata (243)", "'Level' == <wdata>"),
    ],
)
def test_operand_names_of_every_shape(tmp_path, d, text, symbolic):
    check = to_check(d, str(tmp_path))
    assert (check.text, check.symbolic_text) == (text, symbolic)
    record = run_record([case_record(_recorded(check))])
    jsonschema.validate(record, SCHEMA)
    if symbolic is not None:  # the procedure never shows this run's name
        for fmt in ("text", "md", "html"):
            assert "reg3" not in render(record, "procedure", fmt).split("</style>")[-1]


def _recorded(check):
    with Recorder() as rec:
        pass
    rec.unscoped.append(check)
    return rec


def test_the_procedure_names_the_expected_value_whatever_the_description_shows(tmp_path):
    # pytest-verifier shortened the expected name in its description: the report
    # reads as the description has it, the procedure is built from the name itself.
    cut = _named(expected_name="w" * 300, description="Verify 'dev.reg3.F' == www… (243)")
    check = to_check(cut, str(tmp_path))
    assert (check.text, check.symbolic_text) == (
        "dev.reg3.F == www… (243)", f"dev.reg<channel>.F == <{'w' * 300}>")
    assert check.operands is not None and check.operands["expected"] == {"concrete": "w" * 300, "symbolic": "w" * 300}
    # An approx keeps its tolerances: the description's last parts, one per tolerance.
    d = _named(check_type="approx", units="V", expected_name=f"table[3].{'n' * 300}", abs_tol=0.5, rel_tol=0.01,
               expected_name_symbolic=f"table[<channel>].{'n' * 300}",
               description="Verify 'dev.reg3.F' == table[3].nnn… (3.3V) ± 0.5V (abs) ± 1% (rel)")
    check = to_check(d, str(tmp_path))
    assert check.text == "dev.reg3.F == table[3].nnn… (3.3V) ± 0.5V (abs) ± 1% (rel)"
    assert check.symbolic_text == f"dev.reg<channel>.F == table[<channel>].{'n' * 300} V ± 0.5V (abs) ± 1% (rel)"
    # Without them in the record the tolerance is not known: worded as without the name.
    d = _named(check_type="approx", description="Verify 'dev.reg3.F' == www… (3.3) ± 0.5")
    check = to_check(d, str(tmp_path))
    assert check.symbolic_text == "dev.reg<channel>.F == www… (3.3) ± 0.5" and "expected" not in check.operands
    # Line breaks and control characters in a name read as a space: one line in every document.
    d = _named(name="dev.reg3\t.F", actual_name=" dev.reg3\t.F\n", actual_name_symbolic="dev.reg<channel>\t.F",
               expected_name="two\nlines\x85here\ud800", description="Verify 'dev.reg3\\t.F' == two\\nlines (243)")
    check = to_check(d, str(tmp_path))
    assert check.text == "dev.reg3 .F == two\\nlines (243)"
    assert check.symbolic_text == "dev.reg<channel> .F == <two lines here>"
    assert check.operands == {
        "actual": {"concrete": "dev.reg3 .F", "symbolic": "dev.reg<channel> .F"},
        "expected": {"concrete": "two lines here", "symbolic": "two lines here"},
    }
    render(run_record([case_record(_recorded(check))]), "procedure", "html").encode("utf-8")
    # Other spaces and invisible characters are the name's own.
    d = _named(actual_name="dev\u00a0reg\u200b3", actual_name_symbolic=None, expected_name=None,
               name="dev\u00a0reg\u200b3", description="Verify 'dev\u00a0reg\u200b3' == 243")
    assert to_check(d, str(tmp_path)).text == "dev\u00a0reg\u200b3 == 243"


def test_operand_names_that_cannot_be_worded_are_left_out(tmp_path):
    # A description of another shape: as before.
    odd = to_check(_named(description="something else"), str(tmp_path))
    assert (odd.text, odd.symbolic_text, odd.operands) == ("something else", None, None)
    # Names that are not text, or blank: as before.
    for bad in ("", "   ", "\n\t", 3, None, ["x"]):
        d = _named(actual_name=bad, expected_name=bad, name="N", auto_name=None, description="Verify 'N' == 243")
        check = to_check(d, str(tmp_path))
        assert (check.text, check.symbolic_text, check.operands) == ("'N' == 243", None, None)
    # ``contains`` takes no expected name: one in a record is not used.
    d = _named(check_type="contains", description="Verify 'dev.reg3.F' contains wdata (243)")
    assert to_check(d, str(tmp_path)).symbolic_text == "dev.reg<channel>.F contains wdata (243)"


def test_children_are_worded_with_their_operand_names(tmp_path):
    children = [_named(name=f"reg{ch}.F", actual_name=f"reg{ch}.F", actual_name_symbolic="reg<ch>.F",
                       description=f"Verify 'reg{ch}.F' == wdata (243)", passed=False) for ch in (0, 1)]
    d = {"check_type": "all_satisfy", "name": "All", "passed": False, "child_checks": children,
         "description": "Verify all items in 'All' satisfy condition (2 items)"}
    record = run_record([case_record(_recorded(to_check(d, str(tmp_path))))])
    jsonschema.validate(record, SCHEMA)
    # The items read the same in the procedure, so they are one line (CHK-11); the
    # report lists the failed ones with this run's names.
    assert render(record, "procedure", "text").splitlines()[-1] == (
        "   - Verify 'All': for each item, reg<ch>.F == <wdata>"
    )
    assert render(record, "report", "text").splitlines()[-3:] == [
        "   - Verify 'All': for each item, reg<ch>.F == <wdata> -> 2 of 2 failed   ✗",
        "      - Verify item [0]: reg0.F == wdata (243) -> got 240   ✗",
        "      - Verify item [1]: reg1.F == wdata (243) -> got 240   ✗",
    ]


def test_records_without_operand_names_read_as_before(tmp_path):
    """A record of an older pytest-verifier reads as it did; its name is words in
    Markdown and HTML. A run record written before ``label`` is all code there."""
    old = {"check_type": "equal", "name": "HTTP status", "description": "Verify 'HTTP status' == 201",
           "passed": True, "actual": 201, "expected": 201}
    check = to_check(old, str(tmp_path))
    assert (check.text, check.symbolic_text, check.operands, check.label) == (
        "'HTTP status' == 201", None, None, "HTTP status")
    record = run_record([case_record(_recorded(check))])
    entry = record["tests"][0]["unscoped"][0]
    assert "operands" not in entry and entry["label"] == "HTTP status"
    assert "- Verify 'HTTP status' == 201 -> got 201   ✓" in render(record, "report", "text")
    assert "- Verify 'HTTP status' `== 201` → `got 201` ✓" in render(record, "report", "md")
    html = render(record, "report", "html")
    assert 'Verify <span class="label">&#x27;HTTP status&#x27;</span> <code class="expr">== 201</code>' in html
    del entry["label"]
    assert "- Verify 'HTTP status' == 201 -> got 201   ✓" in render(record, "report", "text")
    assert "- Verify `'HTTP status' == 201` → `got 201` ✓" in render(record, "report", "md")
    assert 'Verify <code class="expr">&#x27;HTTP status&#x27; == 201</code>' in render(record, "report", "html")


def test_markdown_code_spans_hold_backticks_and_pipes(tmp_path):
    d = _named(name="a`b``c | d", actual_name="a`b``c | d", actual_name_symbolic=None, expected_name=None,
               description="Verify 'a`b``c | d' == 243")
    record = run_record([case_record(_recorded(to_check(d, str(tmp_path))))])
    assert "- Verify ``` a`b``c | d == 243 ```" in render(record, "procedure", "md")
    assert '<code class="expr">a`b``c | d == 243</code>' in render(record, "procedure", "html")


# --------------------------------------------------------------------------- #
# comment_note
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "source, line, expected",
    [
        ("# one\n# two\nx = 1  # trailing\n", 3, "trailing"),
        ("# one\n#   two  \nx = 1\n", 3, "one two"),
        ("# one\n\nx = 1\n", 3, None),
        ("# noqa: E501\n# TODO later\n# fmt: off\n# type: ignore\nx = 1  # pragma: no cover\n", 5, None),
        ("# Ready\n# FIXME: flaky\nx = 1\n", 3, "Ready"),
        ("# x = 2\n# print(x)\nx = 1\n", 3, None),
        ("#\nx = 1  #   \n", 2, None),
        ('s = """\n# not a comment\n"""\nx = 1\n', 4, None),
        ("def f(a):\n    # In a function\n    return a\n", 3, "In a function"),
        ("y = (\n    1,  # inner\n)\n", 2, None),
        ("x = '# text'\n", 1, None),
        # Labels and keyword-led prose are notes; code with punctuation is not.
        ("# Expected: 1\nx = 1\n", 2, "Expected: 1"),
        ("# State: idle\nx = 1\n", 2, "State: idle"),
        ("# return code\n# raise alarm\n# global reset\n# pass\nx = 1\n", 5,
         "return code raise alarm global reset pass"),
        ("# x: list[int]\n# y: int = 2\n# return f(x)\n# assert x == 1\nx = 1\n", 5, None),
        # A tool directive after the note is not part of it.
        ("x = 1  # CMD FIFO empty  # type: ignore[attr-defined]\n", 1, "CMD FIFO empty"),
        ("x = 1  # trailing B  # noqa: E501\n", 1, "trailing B"),
        # A comment that ends a deeper block belongs to that block.
        ("if x:\n    y = 1\n    # end of branch\nz = 10\n", 4, None),
        ("if x:\n    y = 1\n# about z\nz = 10\n", 4, "about z"),
    ],
)
def test_comment_note_rules(tmp_path, source, line, expected):
    path = tmp_path / "src_notes.py"
    path.write_text(source)
    clear_caches()
    assert comment_note(str(path), line) == expected


def test_comment_note_follows_the_coding_cookie(tmp_path):
    path = tmp_path / "lat.py"
    path.write_bytes(b"# -*- coding: latin-1 -*-\nx = 1\n\n# caf\xe9 note\nverify.equal(x, 1)\n")
    clear_caches()
    assert comment_note(str(path), 5) == "caf\u00e9 note"


def test_comment_note_of_a_multiline_statement_and_missing_files(tmp_path):
    source = "# Above\nvalue = max(\n    1,\n    2,\n)\n"
    path = tmp_path / "multi.py"
    path.write_text(source)
    clear_caches()
    assert comment_note(str(path), 4) == "Above"  # any line of the statement
    assert comment_note(str(tmp_path / "nope.py"), 1) is None
    bad = tmp_path / "bad.py"
    bad.write_text("# Note\nx = (\n")
    assert comment_note(str(bad), 2) in (None, "Note")  # never raises


def test_recorder_notes_for_asserts_and_the_switch():
    with Recorder() as rec:
        with pytest.raises(AssertionError):
            with step("S"):
                assert 1 == 2  # one is not two
    assert rec.steps[0].checks[0].note is None  # looked up when the recorder resolves
    rec2 = Recorder(comment_notes=False)
    here = Path(__file__)
    line = next(i for i, t in enumerate(here.read_text().splitlines(), 1) if "# one is not two" in t)
    for r in (rec, rec2):
        c = Check(text="1 == 2", passed=False, location=Location(str(here), line))
        c.note_from_comment = True
        r.unscoped.append(c)
        r.resolve()
    assert rec.steps[0].checks[0].note == "one is not two"
    assert rec.unscoped[-1].note == "one is not two"
    assert rec2.unscoped[-1].note is None
    record = run_record([case_record(rec)])
    jsonschema.validate(record, SCHEMA)
    assert "(one is not two)" in render(record, "procedure", "text")


def test_only_test_parameters_stay_generic(project):
    """SYM-9: only a value that comes from a test parameter stays ``<name>`` in
    the procedure; constants, local variables, other objects' attributes and
    fixtures show this run's value."""
    project.makepyfile(
        limits="MAX_TX = 8\n\n\nclass Cfg:\n    gain = 2\n",
        test_consts='''
        import pytest
        import limits
        from limits import MAX_TX
        from stepdoc import step

        MY_CONST = 1


        def check_tx(verify, dat, want):
            verify.equal(dat.tx8e, want)


        @pytest.mark.parametrize("channel", [3])
        def test_consts(verify, tb, channel):
            dat = getattr(tb.dev.block, f"esmi{channel}_32dat")
            expected = 1
            offset = channel - 2
            cfg = limits.Cfg()
            with step("Verify"):
                verify.equal(dat.txfe, MY_CONST)
                verify.equal(dat.tx8e, expected)
                verify.equal(dat.txfe, offset)
                verify.between(dat.txfe, MY_CONST - 1, limits.MAX_TX * cfg.gain)
                verify.between(dat.tx8e, channel - 3, MAX_TX)
                verify.between(dat.txfe, 0, len(tb.dev.on_read) + 10)
                check_tx(verify, dat, expected)
                for i in [1]:
                    verify.equal(dat.txfe, i)
        ''',
    )
    run(project).assert_outcomes(passed=1)
    assert numbered(lines(project, "procedure.txt")) == [
        "1. Verify",
        "   1.1. Verify dev.block.esmi<channel>_32dat.txfe == 1",
        "   1.2. Verify dev.block.esmi<channel>_32dat.tx8e == 1",
        "   1.3. Verify dev.block.esmi<channel>_32dat.txfe == <offset>",
        "   1.4. Verify dev.block.esmi<channel>_32dat.txfe ∈ [0, 16]",
        "   1.5. Verify dev.block.esmi<channel>_32dat.tx8e ∈ [<channel - 3>, 8]",
        "   1.6. Verify dev.block.esmi<channel>_32dat.txfe ∈ [0, 11]",
        "   1.7. Verify dev.block.esmi3_32dat.tx8e == <want>",  # a helper: its inputs stay generic
        "   1.8. Verify dev.block.esmi<channel>_32dat.txfe == <i>",
    ]
    report = lines(project, "report.txt")
    assert "   1.1. Verify dev.block.esmi3_32dat.txfe == 1 -> got 1   ✓" in report
