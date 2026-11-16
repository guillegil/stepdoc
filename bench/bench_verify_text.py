"""Cost of verify lines that read like the code (CHK-9, CHK-10).

Run: ``python bench/bench_verify_text.py`` (``--quick`` for fewer repeats).

A test module written to a temporary folder runs 10,000 passes of a register
read plus a verify check, the way pytest-verifier makes them: the check is a
descriptor dict with its ``location``, built on the line that reads the
register. Three loops:

* ``one step``: every pass in one step, the same register (the read polls);
* ``4 channels``: one step, the register of channel ``i % 4`` (a collapsed loop);
* ``1000 steps``: ``with step(...)`` inside the loop, 1,000 steps of 10 passes.

It prints, per loop:

* **record**: µs per pass while the test runs (the bridge recording the read and
  the fake verify building its descriptor), with no recorder, with stepdoc
  recording, and with ``stepdoc_comment_notes`` off;
* **place**: ms to place all checks in their steps after the phase (what the
  verify adapter does in ``pytest_verify_results``), cold caches;
* **resolve**: ms for the case record (symbolic values and comment notes), cold.

To compare with stepdoc before this feature, run it with that version first on
``PYTHONPATH`` (``git archive <rev> src | tar -x -C /tmp/old``, then
``PYTHONPATH=/tmp/old/src python bench/bench_verify_text.py``); options it lacks
are shown as ``-``.
"""

from __future__ import annotations

import gc
import importlib
import os
import platform
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import stepdoc
from stepdoc.adapters import _source
from stepdoc.adapters import verify as verify_adapter
from stepdoc.core import symbolic
from stepdoc.core.record import case_record

N = 10_000
stepdoc.set_skip_modules(["__main__"])  # the fake device and its bridge live here
REPEATS = 2 if "--quick" in sys.argv else 5

TEST_MODULE = '''
import os
import sys

from stepdoc import step


class Verify:
    """What pytest-verifier records: a descriptor per check, with its location."""

    def __init__(self, root):
        self.root = root
        self.records = []

    def equal(self, actual, expected, *, name=None, note=None):
        frame = sys._getframe(1)
        d = {
            "check_type": "equal", "passed": actual == expected, "phase": "call",
            "location": f"{os.path.relpath(frame.f_code.co_filename, self.root)}:{frame.f_lineno}",
            "actual": actual, "expected": expected,
        }
        if name is None:
            name = "dat.txfe"  # pytest-verifier reads it from the source, once per line
            d["auto_name"] = True
        if note:
            d["note"] = note
        d["name"] = name
        d["description"] = f"Verify '{name}' == {expected!r}"
        self.records.append(d)
        return d


def one_step(verify, tb, n):
    dat = getattr(tb.dev.block, f"esmi{3}_32dat")
    with step("Verify CMD FIFO flags"):
        for _ in range(n):
            # CMD FIFO empty
            verify.equal(dat.txfe, 1)


def four_channels(verify, tb, n):
    with step("Verify every channel"):
        for i in range(n):
            dat = getattr(tb.dev.block, f"esmi{i % 4}_32dat")
            verify.equal(dat.txfe, 1)  # CMD FIFO empty


def many_steps(verify, tb, n):
    dat = getattr(tb.dev.block, f"esmi{3}_32dat")
    for i in range(n // 10):
        with step("Round"):
            for _ in range(10):
                verify.equal(dat.txfe, 1, note="CMD FIFO empty")
'''


class Reg:
    def __init__(self, dev: "Dev", path: str) -> None:
        self._dev, self._path = dev, path

    @property
    def txfe(self) -> int:
        self._dev.hook(f"{self._path}.txfe", 1)
        return 1


class Block:
    def __init__(self, dev: "Dev") -> None:
        for ch in range(4):
            setattr(self, f"esmi{ch}_32dat", Reg(dev, f"dev.block.esmi{ch}_32dat"))


class Dev:
    def __init__(self, record: bool) -> None:
        self.hook: Callable[[str, Any], Any] = (
            (lambda path, value: stepdoc.record_action(path, "read", value)) if record else (lambda p, v: None)
        )
        self.block = Block(self)


class Testbench:
    def __init__(self, record: bool) -> None:
        self.dev = Dev(record)


def clear_caches() -> None:
    """Cold start: forget every parsed file and analysed location."""
    symbolic.clear_caches()
    for module in (_source, verify_adapter, sys.modules.get("stepdoc.core.comments"), sys.modules.get("stepdoc.core.record")):
        for value in vars(module).values() if module is not None else ():
            if hasattr(value, "cache_clear"):
                value.cache_clear()


def recorder(notes: bool) -> stepdoc.Recorder:
    try:
        return stepdoc.Recorder("bench", comment_notes=notes)
    except TypeError:  # stepdoc before comment notes
        if not notes:
            raise
        return stepdoc.Recorder("bench")


def run_once(mod: Any, fn: str, root: str, mode: str) -> tuple[float, float, float, Any]:
    """One run of ``fn``: (record µs/pass, place ms, resolve ms, record)."""
    clear_caches()
    gc.collect()
    v = mod.Verify(root)
    tb = Testbench(record=mode != "none")
    if mode == "none":
        t0 = time.perf_counter()
        getattr(mod, fn)(v, tb, N)
        return (time.perf_counter() - t0) / N * 1e6, 0.0, 0.0, None
    rec = recorder(notes=mode == "notes")
    with rec:
        t0 = time.perf_counter()
        getattr(mod, fn)(v, tb, N)
        t1 = time.perf_counter()
    rec.resolve()
    placer = verify_adapter._Placer(rec, root)
    for d in v.records:
        placer.place(d, "procedure")
    t2 = time.perf_counter()
    record = case_record(rec, root=root)
    t3 = time.perf_counter()
    return (t1 - t0) / N * 1e6, (t2 - t1) * 1e3, (t3 - t2) * 1e3, record


def best(mod: Any, fn: str, root: str, mode: str) -> tuple[float, float, float, Any]:
    runs = [run_once(mod, fn, root, mode) for _ in range(REPEATS)]
    return (min(r[0] for r in runs), min(r[1] for r in runs), min(r[2] for r in runs), runs[-1][3])


def first_lines(record: Any) -> list[str]:
    from stepdoc.renderers.common import procedure_lines

    return procedure_lines(record)[:2]


def main() -> None:
    has_notes = "comment_notes" in stepdoc.Recorder.__init__.__code__.co_varnames
    with tempfile.TemporaryDirectory() as root:
        path = Path(root) / "bench_verify_module.py"
        path.write_text(TEST_MODULE)
        sys.path.insert(0, root)
        try:
            mod = importlib.import_module("bench_verify_module")
            rows = []
            for label, fn in (("one step", "one_step"), ("4 channels", "four_channels"), ("1000 steps", "many_steps")):
                none = best(mod, fn, root, "none")
                on = best(mod, fn, root, "notes")
                off = best(mod, fn, root, "plain") if has_notes else None
                rows.append((label, none, on, off))
            sample = first_lines(rows[0][2][3])
        finally:
            sys.path.remove(root)
    where = os.path.dirname(stepdoc.__file__)
    print(f"stepdoc from {where}")
    print(f"Python {platform.python_version()}, {N:,} passes, best of {REPEATS}, cold caches for place/resolve")
    print()
    head = f"{'loop':<12}{'record µs/pass':>34}{'place ms':>20}{'resolve ms':>20}"
    sub = f"{'':<12}{'no rec':>10}{'notes on':>12}{'notes off':>12}{'on':>10}{'off':>10}{'on':>10}{'off':>10}"
    print(head)
    print(sub)

    def cell(value: Any, width: int, digits: int = 2) -> str:
        return f"{'-':>{width}}" if value is None else f"{value:>{width}.{digits}f}"

    for label, none, on, off in rows:
        print(
            f"{label:<12}{cell(none[0], 10)}{cell(on[0], 12)}{cell(off and off[0], 12)}"
            f"{cell(on[1], 10, 1)}{cell(off and off[1], 10, 1)}"
            f"{cell(on[2], 10, 1)}{cell(off and off[2], 10, 1)}"
        )
    print()
    print("procedure (one step):", " | ".join(line.strip() for line in sample))


if __name__ == "__main__":
    main()
