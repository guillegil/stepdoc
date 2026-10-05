"""Bridges from the two fakes to stepdoc, written the way a user would (brief §9)."""

from __future__ import annotations

import json

import httpx
import pytest

import stepdoc
from tests.fakes.fake_api import fake_api_transport
from tests.fakes.fake_device import Device

# Equivalent of the `stepdoc_skip_modules` ini option (SYM-2).
stepdoc.set_skip_modules(["httpx", "tests.fakes"])


def http_bridge(r: httpx.Response) -> None:
    req = r.request
    body = json.loads(req.content) if req.content else None
    stepdoc.record_action(
        "{method} {url}",
        "request",
        body,
        value_from=("json", "content", "data"),
        target_from=("url",),
        method=req.method,
        url=req.url.path,
        status=r.status_code,
    )


async def async_http_bridge(r: httpx.Response) -> None:
    http_bridge_inner(r)


def http_bridge_inner(r: httpx.Response) -> None:
    req = r.request
    body = json.loads(req.content) if req.content else None
    stepdoc.record_action(
        "{method} {url}", "request", body,
        value_from=("json",), target_from=("url",), bridge_frames=2,
        method=req.method, url=req.url.path, status=r.status_code,
    )


@pytest.fixture
def rec():
    with stepdoc.Recorder() as r:
        yield r


@pytest.fixture
def api():
    with httpx.Client(
        base_url="http://api.test",
        transport=fake_api_transport(),
        event_hooks={"response": [http_bridge]},
    ) as client:
        yield client


@pytest.fixture
def dev():
    d = Device.simulated()
    d.on_write(lambda reg, val: stepdoc.record_action(reg.path, "write", val, value_from=("value",)))
    d.on_read(lambda reg, val: stepdoc.record_action(reg.path, "read", val))
    return d
