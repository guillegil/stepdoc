# Spike: symbolic value resolution

Roadmap milestone "Spike" (brief §14): prototype symbolic resolution with
`executing` against two fakes, an HTTP API through httpx event hooks and a
device with read/write events, including helper functions, and benchmark the
cost per event.

**Verdict: feasible and cheap enough.** Resolution works for every pattern in
the brief's examples on Python 3.10 to 3.14, and the hot path costs about
2.5 to 3.5 µs per event on 3.11+. The design below is ready to grow into v0.1.

## What was built

```
src/stepdoc/core/symbolic.py   capture (hot path) + resolution (cached) + expression rendering
src/stepdoc/core/recorder.py   Recorder, step() (context manager and decorator), record_action()
src/stepdoc/core/model.py      Action, Step, Location (subset of brief §8)
src/stepdoc/renderers/text.py  throwaway text renderer for procedure and report
tests/fakes/                   fake user API (httpx.MockTransport) and fake device with register events
tests/conftest.py              the two bridges, written as a user would
bench/bench_events.py          cost per event
examples/spike_demo.py         prints both documents for both examples
```

`stepdoc.core` never imports pytest; `tests/test_core_boundary.py` runs it with
pytest blocked (NFR-9).

## Design: capture now, resolve later

1. **Capture, at event time.** `record_action` walks outward from the bridge
   to the first frame whose module is not in the skip list and stores only
   `(code, f_lasti, f_lineno, f_globals)` plus the code object of the frame it
   called into. No AST work. Whether a code object is skipped is cached per
   code object.
2. **Resolve, at render time.** `executing` maps `(code, lasti)` to an AST node.
   `executing` only reads `f_code`, `f_lasti`, `f_lineno` and `f_globals`, so
   the stored site stands in for the frame and no frame is kept alive.
   Node analysis and rendered text are cached per code location.

Resolution classifies the node:

| Node at the user's line | Value expression used |
|---|---|
| `dev.map.x = <expr>` (also `+=`, annotated, tuple unpacking) | right-hand side |
| `api.post("/users", json=...)`, `dev.write("x", v)` | the argument the bridge names with `value_from`, or the only argument |
| `vout = dev.map.adc.value` (a read) | no value; the read is bound to `<vout>` |
| anything else, or no source | concrete value, marked non-symbolic (SYM-5) |

Rendering (SYM-3 and SYM-7): literals stay literal (`= 100`), other expressions
become `<expr>` (`<width * 4>`), and dict and list literals and f-strings are
rendered part by part (`{"name": <name>, "age": 3}`, `/users/<user_id>`).
SYM-7 came almost for free, so it could move from v0.2 to v0.1.

## Output

`python examples/spike_demo.py` (the fake API stores age 0 as missing, as in brief §10.1):

```
--- Procedure: user_lifecycle
1. Create user
   - POST /users  {"name": <name>, "age": <age>}
2. Read user back
   - GET /users/<user_id>

--- Executed report: user_lifecycle ('Ana', 0)
1. Create user   [passed]
   - POST /users  {'name': 'Ana', 'age': 0}  -> 201
2. Read user back   [failed]
   - GET /users/1  -> 200

--- Procedure: output_by_mode
1. Select mode and level
   - dev.map.ctrl.mode = <mode>
   - dev.map.dac.level = <level>
2. Wait for PLL lock
   - Read dev.map.status.pll_locked
   - Read dev.map.status.pll_locked
   - Read dev.map.status.pll_locked
3. Check output
   - Read dev.map.adc.value -> <vout>
```

## Benchmark

`python bench/bench_events.py`, 4-core Xeon @ 2.10 GHz, executing 2.2.1, best
of 5 runs. Each event is a register write from a user function through the
fake device (2 library frames plus the bridge lambda). µs per event:

| Scenario | 3.10 | 3.11 | 3.12 | 3.13 | 3.14rc2 |
|---|---:|---:|---:|---:|---:|
| Device write, no stepdoc (baseline) | 0.44 | 0.19 | 0.17 | 0.17 | 0.14 |
| + `record_action`, no active recorder (plugin inert) | 0.47 | 0.32 | 0.31 | 0.27 | 0.23 |
| + recorder, symbolic off | 2.16 | 1.17 | 1.54 | 1.71 | 1.33 |
| **+ recorder, capture site (default)** | **4.05** | **2.50** | **3.54** | **3.80** | **2.94** |
| + capture and resolve at once (warm cache) | 5.92 | 3.70 | 5.66 | 5.91 | 4.80 |
| Bridge behind 10 skipped library frames | 6.31 | 4.15 | 6.54 | 6.90 | 6.10 |
| Bridge behind 50 skipped library frames | 14.22 | 11.02 | 18.61 | 20.41 | 19.43 |
| First resolution of a code location (once) | 221 | 56 | 63 | 62 | 49 |
| First resolution in a new source file (parse, once) | 6711 | 5776 | 5162 | 4980 | 4604 |
| httpx GET through MockTransport, no stepdoc | 165 | 144 | 132 | 134 | 136 |
| httpx GET through MockTransport, recorded | 190 | 143 | 155 | 173 | 157 |

Memory: about 380 bytes per recorded action (457 on 3.10).

What the numbers say:

- **The stack walk is not the problem.** Capture adds 1.3 to 2 µs over
  recording without symbolic values, and each skipped library frame costs
  0.2 to 0.4 µs. About half the per-event cost is building the `Action` itself,
  which is the first thing to slim down if needed.
- **Polling is fine.** 10,000 register reads in one test add about 30 to 50 ms.
- **Resolving lazily wins.** The first resolution of a location costs 50 to
  60 µs (220 µs on 3.10) and a new file about 5 ms; both are paid once.
  Resolving eagerly on every event would add 1 to 2 µs for nothing.
- **For software tests the overhead is noise.** An httpx request through an
  in-memory transport already costs 130 to 165 µs; recording adds between 0 and
  40 µs (the measurement is noisy at this scale), and real network calls dwarf both.

**Proposed NFR-3 budget:** at most 5 µs per event on Python 3.11+ with up to 10
skipped library frames, measured by this benchmark in CI, and below 400 bytes per
event before polling collapse (ACT-4).

## Findings that change the brief

1. **`record_action` needs two hints from the bridge** (ACT-1):
   - `value_from=("json", "content", "data")`: which call argument carries the
     value. Names are bound to positions through the signature of the function
     the user called, taken from the first skipped frame, so `api.post(url, json=…)`
     and `dev.write("x", v)` work with keyword or positional arguments.
   - A target template with `target_from=("url",)`, so `"{method} {url}"` renders
     `GET /users/<user_id>` in the procedure and `GET /users/17` in the report.
2. **The bridge is skipped by frame count, not by module.** Bridges live in
   `conftest.py`, which is user code, and helpers in `conftest.py` must stay
   visible. `record_action(..., bridge_frames=1)` is the default; a bridge that
   delegates to an inner function passes 2. `stepdoc_skip_modules` (SYM-2) then
   skips library frames.
3. **Helpers work with no extra effort, and look-through is cheap.** By default a
   value set inside a helper shows the helper's own names (`<code>`). The
   `@look_through` prototype (SYM-6) substitutes the caller's argument
   expressions, across nested helpers (`<dac_code * 2>`), in about 40 lines.
   It could move earlier than v1.0. Limits: a parameter reassigned inside the
   helper still shows the caller's expression, and parameters left at their
   default keep the helper's name.
4. **`await` needs a fallback.** `executing` cannot place a suspended `await`.
   When the statement holds exactly one `await <call>`, the spike uses that
   call, which makes `httpx.AsyncClient` work. Two awaits in one statement fall
   back to concrete values.
5. **Python 3.10 works but is slower.** `executing` places attribute stores on
   3.10 too (one statement-level fallback is used for the read inside `x.r += 1`).
   First resolution is about 4 times slower and the hot path about 1.5 times.
   Nothing in the spike forces 3.11 (NFR-6 is still open).
6. **Reads and writes need different rendering.** A read's value is an outcome
   and must never appear in the procedure. The spike's renderer keys this on the
   op name `"read"`; v0.1 should make it explicit (for example an
   input/output flag on `Action`) rather than rely on op-name conventions.
7. **Events from other threads are not recorded.** The current recorder lives in
   a `ContextVar`, so a device callback fired from a driver's background thread
   sees no recorder, and even with one it has no user frame to resolve. v0.1
   needs a decision: a process-wide fallback recorder with concrete values, or
   documenting it as a limit.
8. **Resolve before the test ends, not at the end of the session.** `executing`
   rereads files that changed on disk, so a file edited during a long session
   could resolve against the wrong source.

## Known gaps (not needed for the spike)

- C functions between the user and the library (`setattr`, `functools.partial`,
  C-accelerated wrappers) hide the real signature, so positional arguments may
  bind to the wrong name. Keyword arguments always work.
- Generic decorators with `*args, **kwargs` wrappers have the same problem.
- Polling still produces one line per read (ACT-4), and checks, parameters,
  redaction, the JSON record and the pytest plugin are not started.

## Suggested next step

Start v0.1 on this code: the JSON run record (OUT-1) and Markdown renderer (OUT-2)
on top of `Action`/`Step`, then the thin pytest plugin (one recorder per test,
resolve at teardown, `stepdoc_skip_modules` ini option), then checks and
`assert` capture (CHK-1…4), redaction (ACT-9) and dry-run (DRY-1, 2).
