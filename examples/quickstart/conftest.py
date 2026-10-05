"""Bridges for the two example systems: an HTTP API and a register-mapped device.

Run from the repository root:

    uv run pytest examples/quickstart --stepdoc-procedure=out/procedure.md --stepdoc-report=out/report.md
"""

from __future__ import annotations

import json

import httpx
import pytest

import stepdoc
from tests.fakes.fake_api import fake_api_transport
from tests.fakes.fake_device import Device


def http_bridge(r: httpx.Response) -> None:
    req = r.request
    stepdoc.record_action(
        "{method} {url}",
        "request",
        json.loads(req.content) if req.content else None,
        value_from=("json",),
        target_from=("url",),
        method=req.method,
        url=req.url.path,
        status=r.status_code,
    )


@pytest.fixture
def api(stepdoc_dry_run):
    # A real suite would talk to a live service unless stepdoc_dry_run is set.
    with httpx.Client(
        base_url="http://api.test",
        transport=fake_api_transport(),
        event_hooks={"response": [http_bridge]},
    ) as client:
        yield client


@pytest.fixture
def dev(stepdoc_dry_run):
    d = Device.simulated()
    d.on_write(lambda reg, val: stepdoc.record_action(reg.path, "write", val, value_from=("value",)))
    d.on_read(lambda reg, val: stepdoc.record_action(reg.path, "read", val))
    return d
