# Bridges

A bridge is a few lines of user code, usually in `conftest.py`, that calls
`stepdoc.record_action` from the system under test's own events. stepdoc finds the
test line that caused the event and reads its symbolic expression from there.

```python
stepdoc.record_action(
    target,              # what is acted on: "POST /users", "dev.map.pulse.width", "users"
    op,                  # free text: "request", "write", "read", "query", "measure", "call"
    value=None,          # what was sent (or, for direction "out", the result)
    *,
    direction=None,      # "in", "out" or "exchange"; defaults from op, else "in"
    value_from=None,     # call argument(s) at the test line that carry the value
    target_from=(),      # template fields of target to fill from call arguments
    bridge_frames=1,     # frames above record_action that belong to the bridge
    **meta,              # anything else: status=201, unit="V", result=...
)
```

- **direction**: `in` (the test sends `value`), `out` (`value` is a result: a read,
  a measurement), `exchange` (a request; put the response in `meta`, e.g.
  `status=` or `result=`). `read`, `get`, `measure`, `receive`, `consume` default to
  `out`; `request`, `query`, `call` default to `exchange`.
- **value_from**: names or positions of the call argument at the test line, e.g.
  `("json",)` for `api.post("/users", json={...})`. Without it, a call with a single
  argument uses that argument.
- **target_from**: makes `target` a `str.format` template. Each field is filled
  concretely from `meta` and symbolically from the call argument of the same name:
  `"{method} {url}"` with `target_from=("url",)` documents `GET /users/<user_id>`.
- **bridge_frames**: raise it when `record_action` is called from a helper inside
  the bridge (2 for one extra function).
- Values, `meta` and targets are redacted before storage: `password`, `token`,
  `authorization` and similar keys, and bearer tokens, become `***`. Add your own
  with `stepdoc.configure_redaction(keys=[...], patterns=[...])`.

List the libraries the event passes through in `stepdoc_skip_modules`, so the
stack walk skips them and lands on the test:

```toml
[tool.pytest.ini_options]
stepdoc_skip_modules = ["httpx", "sqlalchemy", "mydevice"]
```

## HTTP API (httpx event hooks)

```python
import json

import httpx
import pytest
import stepdoc


def http_bridge(r: httpx.Response) -> None:
    req = r.request
    stepdoc.record_action(
        "{method} {url}", "request",
        json.loads(req.content) if req.content else None,
        value_from=("json",), target_from=("url",),
        method=req.method, url=req.url.path, status=r.status_code,
    )


@pytest.fixture
def api(stepdoc_dry_run):
    transport = fake_api_transport() if stepdoc_dry_run else None   # e.g. httpx.MockTransport
    with httpx.Client(base_url=API_URL, transport=transport,
                      event_hooks={"response": [http_bridge]}) as client:
        yield client
```

## Database (SQLAlchemy engine events)

```python
from sqlalchemy import event


@pytest.fixture
def db(engine):
    def on_execute(conn, cursor, statement, parameters, context, executemany):
        stepdoc.record_action(statement, "query", parameters, value_from=("parameters",))

    event.listen(engine, "before_cursor_execute", on_execute)
    yield engine
    event.remove(engine, "before_cursor_execute", on_execute)
```

Add `"sqlalchemy"` to `stepdoc_skip_modules`.

## CLI (subprocess wrapper)

```python
import subprocess


def cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["mytool", *args], capture_output=True, text=True)
    stepdoc.record_action("mytool", "call", args, value_from=("args",), result=result.returncode)
    return result
```

`cli(["users", "add", name])` documents as `Call mytool ["users", "add", <name>]`.

## Device (register events)

```python
@pytest.fixture(scope="session")
def dev(stepdoc_dry_run):
    d = Device.simulated() if stepdoc_dry_run else Device("TCPIP::10.0.0.5")
    d.on_write(lambda reg, val: stepdoc.record_action(reg.path, "write", val, value_from=("value",)))
    d.on_read(lambda reg, val: stepdoc.record_action(reg.path, "read", val))
    yield d
```

Attribute writes (`dev.map.dac.level = level`) document as
`dev.map.dac.level = <level>`. Reads assigned to a name document as
`Read dev.map.adc.value -> <vout>`.

## Measurements

Record a measurement as an `out` action with its unit in `meta`:

```python
stepdoc.record_action(f"scope.ch{channel}.{kind}", "measure", value, unit="V")
```

## Polling

Repeated reads of the same target from one source line collapse into one
`Poll …` line with a count and duration. Turn it off with
`stepdoc_collapse_repeats = false`.
