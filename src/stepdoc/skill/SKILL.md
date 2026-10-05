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
   expression would read badly or passes through a helper.

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
- `stepdoc.attach_check(check, text)` adds a check to the current step.
- `check=` accepts a `bool`, a callable returning `bool`, a `stepdoc.Check`, or an
  object a registered converter understands. Anything else raises `CheckTypeError`.
- stepdoc never compares values itself.

Details: [references/checks.md](references/checks.md).

## Configuration

```toml
[tool.pytest.ini_options]
stepdoc_skip_modules = ["httpx", "mydevice"]
enable_assertion_pass_hook = true
```

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
- no line says "value from this run" (the source was not found: check
  `stepdoc_skip_modules` or the bridge's `value_from`);
- nothing important is in "Unscoped";
- polling shows as one "Poll …" line, not dozens of reads.

See [references/review.md](references/review.md) for common mistakes and fixes,
and [references/dry-run.md](references/dry-run.md) for running without the real
system.

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
