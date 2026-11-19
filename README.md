# stepdoc

stepdoc turns test code into test documentation. From the same pytest tests it
writes two documents:

- a **procedure**: what the test always does, with symbolic values
  (`dev.map.dac.level = <level>`, `GET /users/<user_id>`), and
- an **executed report**: what one run did, with concrete values, pass/fail per
  step and timing.

Both come from a JSON **run record**, so they can never drift apart from the code.

Status: 0.2 (alpha). See [CHANGELOG.md](CHANGELOG.md). [SPIKE.md](SPIKE.md) has the design
measurements behind it.

stepdoc runs on Python 3.9 and above. On 3.9 and 3.10, placing a value on its
source line the first time costs a few times more than on 3.11+ (once per line);
recording itself costs about the same.

## Quick look

```python
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

```
uv run pytest --stepdoc-procedure=out/procedure.md --stepdoc-report=out/report.md
```

Procedure:

```
1. Create user
   1.1. POST /users  {"name": <name>, "age": <age>}
   1.2. Verify r.status_code == 201
2. Read user back
   2.1. GET /users/<user_id>
   2.2. Verify r.json()["age"] == age
```

Report for the `Ana, 0` case:

```
1. Create user   ✓
   1.1. POST /users  {'name': 'Ana', 'age': 0} -> 201
   1.2. Verify r.status_code == 201   ✓
2. Read user back   ✗ AssertionError: assert None == 0
   2.1. GET /users/1 -> 200
   2.2. Verify r.json()["age"] == age   ✗
```

(Shown as plain text; the Markdown files carry the same content with a parameter
table and a summary.)

Run the full example yourself (one case fails on purpose, it is the bug the
directed case finds):

```bash
uv run pytest examples/quickstart --stepdoc-procedure=out/procedure.md --stepdoc-report=out/report.md
```

## How it works

1. **Steps** come from `with step("…")` blocks (or `@step("…")` on helpers). Nesting
   decides the numbering; actions, checks and nested steps share it (1.1, 1.2, 1.2.1).
   A bare `step("…")` call records a one-line step: 1.2 inside a step, a top-level
   step outside one. An event listener in `conftest.py` can call it on every
   register write, for example. Text between backticks in a title is shown in
   monospace (`` step("Write into `dut.reg1 = 1`") ``); `stepdoc.fmt.mono(text)`
   writes that for any text, so it works in f-strings:
   `step(f"Write into {fmt.mono(reg.path)}")`.
2. **Actions** come from your system under test's own events through a small
   **bridge** in `conftest.py`, for example an httpx event hook or a device's
   register callbacks. stepdoc ships no drivers.
3. **Symbolic values** come from the source: when an action is recorded, stepdoc
   finds the line of your test that caused it and later reads that expression
   with [`executing`](https://github.com/alexmojaki/executing). Literals stay
   literal, names become `<name>`. Targets keep their variable parts too:
   after `reg = getattr(tb.dut.block, f"reg{ch}")`, `reg.value = val` reads
   `dut.block.reg<ch>.value = <val>` (also `tb.dut.regs[i]` → `regs[<i>]`), so every
   parametrized run shares one procedure. This applies to actions recorded with
   `record_action`; a bare `step("…")` title is plain text.
4. **Checks** come from plain `assert` (passing ones need
   `enable_assertion_pass_hook = true`), `step("…", check=...)` and
   `stepdoc.attach_check(...)`. stepdoc never compares values itself.

### Keeping documents readable

- **Setup and teardown.** Steps run by fixtures go into a Setup section (S1, S2…)
  and a Teardown section (T1…), apart from the test's own steps.
- **Polling.** The same read (or the same request) repeated from one source line,
  such as `while not dev.map.status.pll_locked: ...`, becomes one line:
  `Poll dev.map.status.pll_locked`, and in the report `-> True (37 reads, 18 ms)`.
  The same write repeated from one line becomes `Repeat dev.fifo.fifo_data = <cmd>`,
  and in the report `-> (16 times, 2 ms)`.
- **Loops.** When the same group of actions comes back to back (the body of a
  `for` or `while` loop, up to 8 actions per pass), the passes become one entry,
  with one pass inside it; the report adds how many passes ran:

  ```text
  1. Empty FIFO
     1.1. Repeat                                      (report: 16 times, 3 ms)
        1.1.1. Read dev.fifo.status.empty
        1.1.2. dev.fifo.fifo_data = <read_command>
     1.2. Read dev.fifo.status.empty                  (the test that ended the loop)
  ```

  Passing checks made in every pass are part of it. A pass that does something
  else (a failed check, another value written) ends the loop and is shown as it
  ran, after it. Wrap the loop in `stepdoc.repeat(...)` to say what it does and
  what it waits for. With a title the block is a step of its own, at the top
  level or inside another step:

  ```python
  with repeat("Fill the CMD FIFO", until="esmi<channel>_ctl.tx = 1",
              counter="write_count", note="The FIFO holds 512 words; 12 are reserved."):
      write_count = 0
      while esmi_ctl_reg.tx == 0:
          esm_dev.da_read(0x10, timeout=0)
          write_count += 1
  ```

  ```text
  2. Fill the CMD FIFO (repeat until esmi<channel>_ctl.tx = 1, counting write_count)
     Note: The FIFO holds 512 words; 12 are reserved.
     2.1. Read esmi_ctl_reg.tx
     2.2. esm_dev.da_read(0x10, ...)
  ```

  The report adds the outcome and how often to line 2 (`✓ 500 times · 40 ms`).
  Without a title it stays an entry of the step it is in
  (`1.2. Repeat until FIFO is empty`). `until`, `counter` (the variable that counts
  the passes), `inc` (how much it grows each pass: `write_count +4 each pass`) and
  `note` are optional and keyword-only. The last test of the condition belongs to
  the loop, and actions before and after the block never join it. The texts are
  written by hand: stepdoc does not turn `while not fifo_empty` into words.
  Turn all of this off with `stepdoc_collapse_repeats = false`.
- **Notes.** `step("Calibrate", note="Offsets drift after power-up.")` (and
  `repeat(..., note=...)`) puts a sentence for the reader under the step's line,
  in the procedure and the report. A triple-quoted note is dedented.
- **Reads inside checks.** A read on the same line as an `assert` (or a
  pytest-verifier check) is shown on the check's line:
  `Verify dev.map.adc.value == 1024 -> got 1024`.
- **Notes from comments.** A comment at the end of a check's line, or the comment
  lines directly above it, is shown after the check in parentheses, in the
  procedure and the report:

  ```python
  with step("Verify CMD FIFO flags at startup"):
      # CMD FIFO empty
      verify.equal(dat.txfe, 1)
      verify.equal(dat.tx8e, 1, note="at least 8 empty positions")
      assert ctl.tx == 0  # FIFO not full
  ```

  ```text
  1. Verify CMD FIFO flags at startup
     1.1. Verify dev.block.esmi<channel>_32dat.txfe == 1 (CMD FIFO empty)
     1.2. Verify dev.block.esmi<channel>_32dat.tx8e == 1 (at least 8 empty positions)
     1.3. Verify ctl.tx == 0 (FIFO not full)
  ```

  A verify check's `note=` wins over a comment. A blank line ends the block above,
  and only comments indented like the check count (one that ends a deeper block
  belongs to that block). `TODO`, `FIXME`, `noqa`, `type:`, `pragma`, `pylint:`,
  `fmt:` and `isort:` markers, a tool directive after the note
  (`# FIFO empty  # noqa: E501` gives `FIFO empty`), empty comments and
  commented-out code (`# x = 2`, `# print(x)`) are skipped; labels such as
  `# Expected: 1` are notes. Comments are read once per line at the end of each
  test phase, never while it runs, so tools that read the steps when the test
  ends (pytest-reporter) see them. Turn this off with
  `stepdoc_comment_notes = false`.
- **Quiet helpers.** `@step("Reset device", record=False)` keeps the step and its
  checks but drops the actions inside it.
- **Naming a value.** `stepdoc.value("code", level * 2)` returns the value and shows
  it as `<code>` in the procedure, also when it travels through a helper first.

### Bridges

```python
# conftest.py
def http_bridge(r):
    stepdoc.record_action(
        "{method} {url}", "request", json.loads(r.request.content or "null"),
        value_from=("json",),        # which call argument carries the value
        target_from=("url",),        # template fields resolved from call arguments
        method=r.request.method, url=r.request.url.path, status=r.status_code,
    )

@pytest.fixture
def api():
    with httpx.Client(base_url=URL, event_hooks={"response": [http_bridge]}) as client:
        yield client

@pytest.fixture
def dev(stepdoc_dry_run):
    d = Device.simulated() if stepdoc_dry_run else Device("TCPIP::10.0.0.5")
    d.on_write(lambda reg, val: stepdoc.record_action(reg.path, "write", val, value_from=("value",)))
    d.on_read(lambda reg, val: stepdoc.record_action(reg.path, "read", val))
    return d
```

`record_action(target, op, value, *, direction=None, value_from=None, target_from=(), bridge_frames=1, **meta)`

- `op` is free text (`write`, `read`, `request`, `query`, `measure`, …).
- `direction` says which way data flows: `in` (the test sends `value`), `out`
  (`value` is a result, such as a read or a measurement) or `exchange` (a request;
  put the response in `meta`, for example `status=` or `result=`). Common op names
  default sensibly; anything else is `in`.
- Values, `meta` and targets are **redacted** before they are stored: keys such as
  `password`, `token`, `authorization` and bearer tokens become `***`. Extend with
  `stepdoc.configure_redaction(keys=[...], patterns=[...])`.

Tell stepdoc which modules are library code, so it looks past them for your line:

```toml
[tool.pytest.ini_options]
stepdoc_skip_modules = ["httpx", "mydevice"]
enable_assertion_pass_hook = true
```

## Options

| Option | Purpose |
|---|---|
| `--stepdoc-record=PATH` | Write the JSON run record (schema: `src/stepdoc/schema/run-record.schema.json`) |
| `--stepdoc-procedure=PATH` | Write the procedure. The extension picks the format: `.md`, `.html` (one self-contained file) or `.txt` |
| `--stepdoc-report=PATH` | Write the executed report, same formats |
| `--stepdoc-dry-run` | Collect the procedure without judging: assertion failures inside steps are recorded and skipped so later steps are still documented |
| `stepdoc_skip_modules` (ini) | Module prefixes skipped when looking for your source line |
| `stepdoc_collapse_repeats` (ini) | Collapse polling, repeated writes and loops into one entry (default `true`) |
| `stepdoc_comment_notes` (ini) | Show the comment on or above an `assert` or verify check as its note (default `true`) |
| `stepdoc_adapters` (ini) | Adapters to run. Default: every adapter whose tool is installed. `-verify` leaves one out, `none` runs none |

Without any of these options the plugin records nothing and prints nothing.

All options work with pytest-xdist (`-n 4`): each worker records the tests it
runs, and the main process writes one record and one set of documents, with
tests in collection order. Tests on a worker that crashes are missing from the
documents.

The session-scoped `stepdoc_dry_run` fixture (or `stepdoc.is_dry_run(config)`) tells your fixtures
to use a simulated backend. Python evaluates arguments eagerly and dry-run only
documents the branches the simulation takes.

## Adapters

stepdoc works with other pytest plugins without depending on them, and they
don't depend on stepdoc. When one of them is installed, its adapter reads what
the plugin already publishes:

- **pytest-verifier**: each `verify.*` check becomes a "Verify …" line in the
  step it was made in, taken from the `pytest_verify_results` hook. An expected
  value given by name is symbolic in the procedure (`Verify 'Stored age':
  user["age"] == <age>`) and concrete in the report (`== 30 -> got 31 ✗`).
  Comparisons, `between`, `is_true`, `is_false`, `is_none` and `is_not_none` start
  with the checked value: a register read on the check's line is folded into it
  and named by its target (`Verify dev.block.esmi<channel>_32dat.txfe == 1` in the
  procedure, `dev.block.esmi3_32dat.txfe == 1 -> got 1 ✓` in the report),
  otherwise the argument's source (`Verify write_count == 500`). A check made
  without `name=` reads like that; with one, the name comes first:
  `Verify 'FIFO not full': dev.block.esmi<channel>_ctl.tx == 0`. A literal
  checked value keeps the old form (`Verify 'HTTP status' == 201`). The
  check's `note=` is shown after it in parentheses.

  Operand names win over that wording. When the code does not say what a value
  is, pytest-verifier's `actual_name=` and `expected_name=` do, and stepdoc words
  the check with them:

  ```python
  block_reg_f = getattr(tb.dev.map.block, f"reg{channel}").F
  verify.equal(block_reg_f, wdata,
               actual_name=f"tb.dev.map.block.reg{channel}.F", expected_name="wdata")
  ```

  ```text
  procedure   Verify tb.dev.map.block.reg<channel>.F == <wdata>
  report      Verify tb.dev.map.block.reg3.F == wdata (243) -> got 240 ✗
  ```

  The procedure shows only the generic form of a name (each `{...}` of the
  f-string or template string as `<...>`; a name without one as it is written)
  and no value of the run: an expected name reads `<wdata>`, like any expected
  value given by name. The report shows the names as this run spelled them, the
  expected value once, after its name. With `name=` as well, the name comes
  first: `Verify 'Stored word': tb.dev.map.block.reg<channel>.F == <wdata>`.
  `actual_name=` works on every check of one value, `expected_name=` on the
  comparisons and `length`. A read made on the check's line still goes into the
  check; line breaks and control characters in a name read as a space. In the
  run record such a check has `operands` (each name, `concrete`
  and `symbolic`), and a check that starts with its `name=` has `label`; steps
  handed to other plugins carry both as `Check.operands` and `Check.label`.

  In the HTML documents the code of a verify line (the checked expression, the
  comparison, operand names) is monospace, `<code class="expr">`, in the
  system's own monospace font; a name given with `name=` and the note stay in
  the text font. Markdown puts the same code between backticks.
- **pytest-strategies**: the report shows each case's seed and the row it ran
  (`directed: youngest`), and the procedure's parameter table shows what each
  argument draws from (`integer 0 … 120`) instead of the sampled values.

If a plugin that judges checks runs with its adapter off, stepdoc can't see
those checks, so it shows the steps of the tests that use it as `?`, never ✓,
next to the test's own verdict. In dry-run, verify's checks are documented as
not judged, but verify still judges them, so a failing one still fails the test.

Other packages can ship adapters through the `stepdoc.adapters` entry-point
group (a subclass of `stepdoc.adapters.Adapter`). Plugins can also follow steps
without importing stepdoc, through the `pytest_stepdoc_step_started`,
`pytest_stepdoc_step_finished` and `pytest_stepdoc_record_ready` hooks
(implement them with `@pytest.hookimpl(optionalhook=True)`).

## Rendering a saved record

`stepdoc render` turns a run record into documents again, without running the
tests or needing pytest installed:

```bash
stepdoc render out/run.json --procedure procedure.html --report report.md
stepdoc render out/run.json                          # procedure as Markdown on stdout
stepdoc render out/run.json --doc report --format text
```

## Agent skill

stepdoc ships a skill that teaches AI coding agents (Claude Code, Codex, GitHub
Copilot and others) to write tests with stepdoc and review the procedure:

```bash
stepdoc skill install            # .claude/skills/ and .agents/skills/ in this repository
stepdoc skill install --claude   # only .claude/skills/ (--agents for only .agents/skills/)
stepdoc skill install --global   # in your home folder instead
stepdoc skill status             # where it is installed, and whether it is outdated
stepdoc skill uninstall
```

Reinstalling an unchanged copy does nothing; a copy you edited is never
overwritten or removed without `--force`. `--print` shows the skill instead of
writing it.

## Without pytest

`stepdoc.core` never imports pytest, so the same API works in scripts and test stations:

```python
from stepdoc import Recorder, step, dumps

with Recorder("station-42") as rec:
    with step("Configure"):
        ...
open("run.json", "w").write(dumps(rec))
```

## Development

```bash
uv sync
uv run pytest
uv run mypy
uv run python bench/bench_events.py      # cost per recorded event
uv run python bench/bench_verify_text.py # verify checks: recording, placing, resolving
uv run --python 3.11 pytest              # any supported Python (3.9+)
```
