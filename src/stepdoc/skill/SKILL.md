---
name: stepdoc
description: Write and document pytest tests with stepdoc. Use when writing or changing tests that use `step()`, writing a bridge that records actions on the system under test (HTTP client, database, CLI, device), adding checks, running in dry-run, or generating and reviewing test procedures and executed reports.
license: MIT
metadata:
  stepdoc-version: "{{stepdoc_version}}"
---

# stepdoc

stepdoc turns pytest tests into two documents from one source:

- a **procedure** with symbolic values (`dev.map.dac.level = <level>`, `GET /users/<user_id>`), and
- an **executed report** with concrete values, ✓/✗ per step and timing.

The test code is the only source. Write plain pytest; stepdoc reads the structure
from `with step(...)` blocks, the actions from the system under test's own events,
and the symbolic names from the source code.

## Writing a test

```python
import pytest
from stepdoc import step


@pytest.mark.parametrize("name, age", [("Ana", 0), ("Bo", 120)])
def test_user_lifecycle(api, name, age):
    with step("Create user"):
        r = api.post("/users", json={"name": name, "age": age})
        assert r.status_code == 201
        user_id = r.json()["id"]

    with step("Read user back"):
        r = api.get(f"/users/{user_id}")
        assert r.json()["age"] == age
```

Rules that keep the procedure readable:

1. **One `with step("…")` per thing a reviewer would check off.** Titles are short
   imperative phrases: "Create user", "Select mode and level".
2. **Nest steps** for sub-procedures. Nesting decides the numbering (1, 1.1, 1.2.1);
   actions, checks and child steps share it.
3. **Pass named variables, not literals,** when the value is a parameter of the
   test. `dev.map.dac.level = level` documents as `<level>`; `= 2048` documents as
   `2048`. Use literals only for values that never change.
4. **Use `@step("…")` on helpers** that form one logical step (`wait_pll_lock`).
   Add `record=False` when the helper's low-level actions would only add noise.
5. **Keep actions inside steps.** Actions outside any step land in an "Unscoped"
   section.
6. **Assert inside the step it belongs to.** A failing assert is attached to its
   step; a read on the assert's line (`assert dev.map.adc.value == 0`) is shown on
   the check's line.
7. **Name computed values** with `stepdoc.value("code", level * 2)` when the
   expression would read badly or passes through a helper. A helper decorated
   with `@stepdoc.look_through` documents its parameters as the caller's
   argument expressions.
8. **Say why with `note=`** when the title alone does not: `step("Fill the FIFO",
   note="512 words, 12 reserved")`. It is shown under the step's line.
9. **Name loops with `stepdoc.repeat("Drain the FIFO", until="FIFO is empty")`**
   around the `for`/`while`. With a title it is a step of its own (top level or
   nested); add `counter="write_count"` (and `inc=4` when it does not grow by 1)
   when the test counts the passes. See [references/bridges.md](references/bridges.md).

## Bridges: where actions come from

stepdoc ships no drivers or clients. A **bridge** in `conftest.py` connects the
system under test's events to `stepdoc.record_action(target, op, value, **meta)`:

```python
@pytest.fixture
def dev(stepdoc_dry_run):
    d = Device.simulated() if stepdoc_dry_run else Device("TCPIP::10.0.0.5")
    d.on_write(lambda reg, val: stepdoc.record_action(reg.path, "write", val, value_from=("value",)))
    d.on_read(lambda reg, val: stepdoc.record_action(reg.path, "read", val))
    return d
```

List the library modules in `stepdoc_skip_modules` so stepdoc looks past them for
the test's own line. See [references/bridges.md](references/bridges.md) for HTTP,
database, CLI and device recipes and every `record_action` argument.

## Checks

- Plain `assert` works. Passing asserts are recorded only with
  `enable_assertion_pass_hook = true` in the pytest ini.
- `step("Output in range", check=vout < 3.3)` records a one-line step.
- Backticks in a title mark monospace, as in Markdown: `step("Write into `dut.reg1 = 1`")`.
  In f-strings use `fmt.mono` (`from stepdoc import fmt`), which picks a safe fence:
  `step(f"Write into {fmt.mono(reg.path)}")`. HTML outputs show these spans as code.
- A bare `step("dut.reg1 = 45")` statement records a one-line step without a check:
  `1.2` inside an open step, a top-level step outside one. Event listeners can log
  with it. `s = step("…")` kept for a later `with s:` records nothing by itself.
- `stepdoc.attach_check(check, text)` adds a check to the current step.
- `check=` accepts a `bool`, a callable returning `bool`, a `stepdoc.Check`, or an
  object a registered converter understands. Anything else raises `CheckTypeError`.
- A comment directly above a check (or at the end of its line) is its note:
  `Verify dat.txfe == 1 (CMD FIFO empty)`. Write it for the reader; `note=` on a
  verify check wins. `stepdoc_comment_notes = false` turns comment notes off.
- pytest-verifier's `actual_name=` and `expected_name=` word a verify check in
  place of its source: `verify.equal(f, wdata, actual_name=f"blk.reg{ch}.F",
  expected_name="wdata")` reads `Verify blk.reg<ch>.F == <wdata>` in the procedure
  and `blk.reg3.F == wdata (243)` in the report (references/checks.md).
- A register check shows what pytest-verifier shows: `verify.bits(dev.CTRL, wdata,
  mask=MASK, width=16)` reads `Verify dev.CTRL & <MASK> == <wdata>` in the
  procedure and `dev.CTRL & 0x00FF == 0x00F3 -> got 0x12F0, bits [1:0] differ ✗`
  in the report; `radix=`/`width=` on a comparison put its values in hex too
  (pytest-verifier 0.11+, references/checks.md).
- stepdoc never compares values itself.

Details: [references/checks.md](references/checks.md).

## Configuration

```toml
[tool.pytest.ini_options]
stepdoc_skip_modules = ["httpx", "mydevice"]   # libraries between the test and the bridge
enable_assertion_pass_hook = true              # record passing asserts
# stepdoc_collapse_repeats = false             # keep every polled read, repeated write and loop pass
# stepdoc_comment_notes = false                # do not take check notes from comments
# stepdoc_adapters = ["-verify"]               # leave an adapter out; ["none"] runs none
```

The plugin records only when one of its options is given (`--stepdoc-record`,
`--stepdoc-procedure`, `--stepdoc-report`, `--stepdoc-dry-run`).

## Generating and reviewing the documents

```bash
pytest --stepdoc-procedure=out/procedure.md --stepdoc-report=out/report.html --stepdoc-record=out/run.json
pytest --stepdoc-dry-run --stepdoc-procedure=out/procedure.md   # against simulated backends
stepdoc render out/run.json --procedure procedure.html          # again, without running tests
```

The file extension picks the format: `.md`, `.html` or `.txt`.

**After writing or changing a test, generate the procedure and read it.** Check that:

- every step has a clear title and at least one action or check;
- parameters show as `<name>`, not as one run's literal values;
- a register picked by a variable (`getattr(blk, f"reg{ch}")`, `regs[i]`) shows
  as `reg<ch>` / `regs[<i>]`, through one local alias at most a few hops deep;
- no line says "value from this run" (the source was not found: check
  `stepdoc_skip_modules` or the bridge's `value_from`);
- nothing important is in "Unscoped";
- polling shows as one "Poll …" line, and a loop as one "Repeat" entry, not
  dozens of lines. A `verify.*` check made in every pass is the exception: it
  is one line per pass (check once after the loop, or `verify.all_satisfy`).

See [references/review.md](references/review.md) for common mistakes and fixes,
and [references/dry-run.md](references/dry-run.md) for running without the real
system.

## With other plugins

stepdoc imports none of them; an adapter runs when its plugin is installed.

- **pytest-verifier**: `verify.*` checks become "Verify …" lines
  ([references/checks.md](references/checks.md)).
- **pytest-strategies**: the report shows each case's seed and the row it ran
  (`directed: youngest`); the procedure's parameter table shows what each
  argument draws from (`integer 0 … 120`) instead of the sampled values.
- **pytest-xdist** (`-n 4`): workers record, the main process writes one record
  and one set of documents. Tests of a worker that crashed are missing.
- A plugin can follow steps without importing stepdoc: implement
  `pytest_stepdoc_step_started(item, step)`, `pytest_stepdoc_step_finished(item,
  step)` or `pytest_stepdoc_record_ready(config, record)` with
  `@pytest.hookimpl(optionalhook=True)`. They are called only while stepdoc records.
- A package ships its own adapter as an entry point in the `stepdoc.adapters`
  group (a subclass of `stepdoc.adapters.Adapter`).

This skill is installed and updated with `stepdoc skill install` (`stepdoc skill
status` says whether a copy is outdated).

## Without pytest

`stepdoc.core` never imports pytest. In scripts or test stations:

```python
from stepdoc import Recorder, dumps, step

with Recorder("bench run") as rec:
    with step("Configure"):
        ...
open("run.json", "w").write(dumps(rec))
```

Then `stepdoc render run.json --procedure procedure.md`.
