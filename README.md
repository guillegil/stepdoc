# stepdoc

stepdoc turns test code into test documentation. From the same pytest tests it
writes two documents:

- a **procedure**: what the test always does, with symbolic values
  (`dev.map.dac.level = <level>`, `GET /users/<user_id>`), and
- an **executed report**: what one run did, with concrete values, pass/fail per
  step and timing.

Both come from a JSON **run record**, so they can never drift apart from the code.

Status: v0.1 in progress (brief §14). [SPIKE.md](SPIKE.md) has the design
measurements behind it.

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
2. **Actions** come from your system under test's own events through a small
   **bridge** in `conftest.py`, for example an httpx event hook or a device's
   register callbacks. stepdoc ships no drivers.
3. **Symbolic values** come from the source: when an action is recorded, stepdoc
   finds the line of your test that caused it and later reads that expression
   with [`executing`](https://github.com/alexmojaki/executing). Literals stay
   literal, names become `<name>`.
4. **Checks** come from plain `assert` (passing ones need
   `enable_assertion_pass_hook = true`), `step("…", check=...)` and
   `stepdoc.attach_check(...)`. stepdoc never compares values itself.

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
| `--stepdoc-procedure=PATH` | Write the procedure (Markdown) |
| `--stepdoc-report=PATH` | Write the executed report (Markdown) |
| `--stepdoc-dry-run` | Collect the procedure without judging: assertion failures inside steps are recorded and skipped so later steps are still documented |
| `stepdoc_skip_modules` (ini) | Module prefixes skipped when looking for your source line |

Without any of these options the plugin records nothing and prints nothing.

The `stepdoc_dry_run` fixture (or `stepdoc.is_dry_run(config)`) tells your fixtures
to use a simulated backend. Python evaluates arguments eagerly and dry-run only
documents the branches the simulation takes.

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
uv run --python 3.11 pytest              # any supported Python (3.11+)
```
