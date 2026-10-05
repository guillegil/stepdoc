"""Print the procedure and the executed report for both spike examples.

Run: ``python examples/spike_demo.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # for the fakes in tests/

import httpx  # noqa: E402

import stepdoc  # noqa: E402
from stepdoc import step  # noqa: E402
from stepdoc.renderers.text import render_procedure, render_report  # noqa: E402
from tests.conftest import http_bridge  # noqa: E402
from tests.fakes.fake_api import fake_api_transport  # noqa: E402
from tests.fakes.fake_device import Device  # noqa: E402

stepdoc.set_skip_modules(["httpx", "tests.fakes"])


def user_lifecycle(api, name, age):
    with step("Create user"):
        r = api.post("/users", json={"name": name, "age": age})
        user_id = r.json()["id"]
    with step("Read user back"):
        r = api.get(f"/users/{user_id}")
        assert r.json()["age"] == age, f"Stored age is {r.json()['age']!r}"
    with step("Delete user"):
        api.delete(f"/users/{user_id}")


@step("Wait for PLL lock")
def wait_pll_lock(dev):
    while not dev.map.status.pll_locked:
        pass


def output_by_mode(dev, mode, level):
    with step("Select mode and level"):
        dev.map.ctrl.mode = mode
        dev.map.dac.level = level
        wait_pll_lock(dev)  # a @step helper: nests as 1.3
    with step("Check output"):
        vout = dev.map.adc.value
        assert vout == level // 2


def run(title, fn, *args):
    with stepdoc.Recorder() as rec:
        try:
            fn(*args)
        except AssertionError:
            pass
    print(f"--- Procedure: {title}\n{render_procedure(rec)}\n")
    print(f"--- Executed report: {title} {args[1:]}\n{render_report(rec)}\n")


def main():
    with httpx.Client(
        base_url="http://api.test", transport=fake_api_transport(), event_hooks={"response": [http_bridge]}
    ) as api:
        run("user_lifecycle", user_lifecycle, api, "Ana", 0)

    dev = Device.simulated()
    dev.on_write(lambda reg, val: stepdoc.record_action(reg.path, "write", val, value_from=("value",)))
    dev.on_read(lambda reg, val: stepdoc.record_action(reg.path, "read", val))
    run("output_by_mode", output_by_mode, dev, 2, 2048)


if __name__ == "__main__":
    main()
