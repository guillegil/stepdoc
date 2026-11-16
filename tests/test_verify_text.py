"""Verify lines that read like the code (CHK-9, CHK-10).

A check's text starts with the checked value: the register a read on the same
line produced (``dev.block.esmi<channel>_32dat.txfe == 1``), else the argument's
source; a ``note=`` or a source comment is shown after it in parentheses.
pytest-verifier is not installed here: a fake publishes descriptors shaped like
its own, with ``auto_name`` and ``note``."""

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
from tests.test_adapters import SCHEMA, VERIFY_SPECS

pytest_plugins = ["pytester"]

# A fake pytest-verifier whose name= is optional: without one, the name is the
# source of the first argument and the descriptor says ``auto_name``.
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

    def equal(self, actual, expected, *, name=None, note=None):
        return self._add("equal", name, "== " + repr(expected), actual == expected, note,
                         actual=actual, expected=expected)

    def between(self, actual, low, high, *, name=None, note=None):
        return self._add("between", name, f"∈ [{low!r}, {high!r}]", low <= actual <= high, note,
                         actual=actual, low=low, high=high)

    def is_true(self, actual, *, name=None, note=None):
        return self._add("true", name, "is True", bool(actual), note, actual=actual)

    def _add(self, kind, name, rest, passed, note, **fields):
        frame = sys._getframe(2)
        root = str(self.item.config.rootpath)
        d = {"check_type": kind, "phase": "call", "passed": passed,
             "location": f"{os.path.relpath(frame.f_code.co_filename, root)}:{frame.f_lineno}", **fields}
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
    pytester.makepyfile(pytest_verifier=FAKE_VERIFY, verify_specs=VERIFY_SPECS, fakedev=FAKE_DEVICE)
    pytester.makeconftest(CONFTEST)
    pytester.makeini("[pytest]\nstepdoc_skip_modules = fakedev\nenable_assertion_pass_hook = true\n")
    return pytester


def run(pytester, *args):
    out = ("--stepdoc-record=run.json", "--stepdoc-procedure=procedure.txt", "--stepdoc-report=report.txt")
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "pytest_verifier", *out, *args)


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
        "-p", "no:cacheprovider", "-p", "pytest_verifier",
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
    assert check.children[0].note == "first" and check.note is None


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
