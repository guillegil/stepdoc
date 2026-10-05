"""Cost per recorded event (NFR-3). Run: ``python bench/bench_events.py``.

Every scenario writes a device register from a user function through the fake
device (2 library frames + the bridge lambda), unless stated otherwise.
Numbers are the best of several repeats, in microseconds per event.
"""

from __future__ import annotations

import gc
import json
import platform
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

import httpx  # noqa: E402

import stepdoc  # noqa: E402
from stepdoc.core import symbolic  # noqa: E402
from tests.fakes.fake_api import fake_api_transport  # noqa: E402
from tests.fakes.fake_device import Device  # noqa: E402

stepdoc.set_skip_modules(["httpx", "tests.fakes", "bench_deep"])

N = 20_000
REPEATS = 5


def best(fn, n=N, repeats=REPEATS):
    times = []
    for _ in range(repeats):
        gc.collect()
        t0 = time.perf_counter_ns()
        fn(n)
        times.append(time.perf_counter_ns() - t0)
    return min(times) / n / 1000  # µs per event


def make_dev(record: bool, **kw):
    d = Device(**kw)
    if record:
        d.on_write(lambda reg, val: stepdoc.record_action(reg.path, "write", val, value_from=("value",)))
        d.on_read(lambda reg, val: stepdoc.record_action(reg.path, "read", val))
    else:
        d.on_write(lambda reg, val: None)
        d.on_read(lambda reg, val: None)
    return d


def writes(dev):
    def loop(n):
        width = 7
        for _ in range(n):
            dev.map.pulse.width = width

    return loop


def in_recorder(loop, **rec_kw):
    def run(n):
        with stepdoc.Recorder(**rec_kw):
            loop(n)

    return run


def eager(dev):
    def loop(n):
        width = 7
        with stepdoc.Recorder() as rec:
            for _ in range(n):
                dev.map.pulse.width = width
                rec.unscoped[-1].resolve()

    return loop


# Library depth: a chain of frames in a skipped module between user code and the bridge.
DEEP_SRC = """
def make(depth, sink):
    def level(i, v):
        if i == 0:
            sink(v)
        else:
            level(i - 1, v)
    return lambda v: level(depth, v)
"""
deep_mod = type(sys)("bench_deep")
exec(compile(DEEP_SRC, "<bench_deep>", "exec"), deep_mod.__dict__)


def deep_writes(depth):
    call = deep_mod.make(depth, lambda v: stepdoc.record_action("reg", "write", v))

    def loop(n):
        width = 7
        for _ in range(n):
            call(width)

    return in_recorder(loop)


def cold_location():
    """First resolution of a location: executing's node finder, source already parsed."""
    dev = make_dev(True)
    with stepdoc.Recorder() as rec:
        width = 7
        dev.map.pulse.width = width
    site = rec.unscoped[0].site
    site.resolution()  # parse the file once

    def loop(n):
        for _ in range(n):
            symbolic._resolutions.clear()
            for name, value in vars(symbolic.executing.Source).items():
                if name.endswith("executing_cache") and isinstance(value, dict):
                    value.clear()
            site.resolution()

    return loop, site


def cold_file(site):
    def loop(n):
        for _ in range(n):
            symbolic.clear_caches()
            site.resolution()

    return loop


def http(record):
    hooks = {"response": [_http_bridge]} if record else {}
    client = httpx.Client(base_url="http://api.test", transport=fake_api_transport(), event_hooks=hooks)

    def loop(n):
        for _ in range(n):
            client.get("/jobs/1")

    return in_recorder(loop) if record else loop


def _http_bridge(r):
    stepdoc.record_action(
        "{method} {url}", "request", None, target_from=("url",),
        method=r.request.method, url=r.request.url.path, status=r.status_code,
    )


def polling(record):
    def run(n):
        dev = make_dev(record, lock_after=n)
        with stepdoc.Recorder():
            while not dev.map.status.pll_locked:
                pass

    return run


def memory_per_action(n=50_000):
    dev = make_dev(True)
    tracemalloc.start()
    with stepdoc.Recorder() as rec:
        width = 7
        before = tracemalloc.get_traced_memory()[0]
        for _ in range(n):
            dev.map.pulse.width = width
        after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    del rec
    return (after - before) / n


def main():
    rows = []
    base = best(writes(make_dev(False)))
    rows.append(("Device write, no stepdoc (baseline)", base))
    rows.append(("+ record_action, no active recorder (inert)", best(writes(make_dev(True)))))
    rows.append(("+ recorder, symbolic off", best(in_recorder(writes(make_dev(True)), symbolic=False))))
    rows.append(("+ recorder, capture site (deferred, default)", best(in_recorder(writes(make_dev(True))))))
    rows.append(("+ capture and resolve at once (eager, warm cache)", best(eager(make_dev(True)))))
    for depth in (0, 10, 50):
        rows.append((f"Bridge behind {depth} skipped library frames, capture", best(deep_writes(depth))))
    loop, site = cold_location()
    rows.append(("Resolve a new location (once per location)", best(loop, n=500)))
    rows.append(("Resolve a new file (parse + location, once per file)", best(cold_file(site), n=50)))
    rows.append(("httpx GET via MockTransport, no stepdoc", best(http(False), n=2000)))
    rows.append(("httpx GET via MockTransport, recorded", best(http(True), n=2000)))
    rows.append(("Polling read, no stepdoc", best(polling(False))))
    rows.append(("Polling read, recorded", best(polling(True))))

    print(f"Python {platform.python_version()} on {platform.machine()}, executing {symbolic.executing.__version__}\n")
    print("| Scenario | µs/event | Δ vs baseline |")
    print("|---|---:|---:|")
    for name, us in rows:
        delta = f"+{us - base:.2f}" if name.startswith("+") or "frames" in name else ""
        print(f"| {name} | {us:.2f} | {delta} |")
    print(f"\nMemory per recorded action (deferred): {memory_per_action():.0f} bytes")


if __name__ == "__main__":
    main()
