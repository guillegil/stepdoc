# Pre-conditions

A pre-condition is a named state the system must be in before the test, such as
"System Mode A", which involves several devices and is shared by many tests.
`stepdoc.precondition(title, *, note=None)` records it in its own
**Pre-conditions** section, before Setup: the block is `P1`, what is recorded
inside it (actions, checks, steps) `P1.1`, `P1.2`…

Use it when a reviewer would ask "in which state does this test start?". Leave
bench preparation without such a name (open a connection, clear a log) as plain
fixture steps: they go to Setup.

## On a fixture

```python
import pytest
import stepdoc
from stepdoc import step


@pytest.fixture(scope="session")
@stepdoc.precondition("System Mode A", note="DEV1 drives the bus; DEV2 and DEV3 listen.")
def mode_a(dev1, dev2, dev3):
    dev1.write("ctrl.mode", 1)
    dev2.write("ctrl.standby", 1)
    dev3.write("ctrl.standby", 1)
    assert dev1.regs["ctrl.mode"] == 1
    yield
    with step("Leave Mode A"):
        dev1.write("ctrl.mode", 0)
```

- `@pytest.fixture` goes on top, `@stepdoc.precondition(...)` directly on the
  function.
- With a `yield`, only the part before it is the pre-condition. What runs after
  it is ordinary teardown (`T1. Leave Mode A`).
- Without a `yield` the whole function is the pre-condition. The decorator works
  on plain helpers, coroutine functions and async generators too.

## As a block

```python
def test_receive(mode_a, dev2):
    with stepdoc.precondition("Receiver enabled"):
        dev2.write("rx.enable", 1)
    with step("Read a frame"):
        dev2.write("rx.pop", 1)
```

The section does not depend on where the block runs. In a fixture the code
around the block stays in Setup; in a test body the steps around it stay in the
procedure.

## What the documents show

```text
test_bus.py::test_send
Pre-conditions
P1. System Mode A
   Note: DEV1 drives the bus; DEV2 and DEV3 listen.
   P1.1. dev1.ctrl.mode = 1
   P1.2. dev2.ctrl.standby = 1
   P1.3. dev3.ctrl.standby = 1
   P1.4. Verify dev1.regs["ctrl.mode"] == 1
Procedure
1. Send a frame
   1.1. dev1.tx.data = 85

test_bus.py::test_receive
Pre-conditions
P1. System Mode A   already established, applied in test_bus.py::test_send
   Note: DEV1 drives the bus; DEV2 and DEV3 listen.
P2. Receiver enabled
   P2.1. dev2.rx.enable = 1
Procedure
1. Read a frame
   1.1. dev2.rx.pop = 1
```

- **Already established.** pytest runs a session, module or class fixture once
  and reuses it. A later test that uses the fixture lists its pre-condition
  without entries, naming the test that applied it; the report adds the status
  it got there (`✓ already established, applied in …`). Nothing to write for
  this: request the fixture (as an argument or with `usefixtures`). A
  function-scoped fixture runs for every test, so each test shows it in full.
- Pre-conditions found established come first, then the ones the test applied.
- A parametrized test's procedure shows the pre-condition in full when one of
  its cases applied it.

## Rules

- **Not inside a step.** A block written inside `with step(...)`, or a decorated
  helper called from one, is not an entry of that step: it becomes the next
  top-level pre-condition and the step goes on after it. Write it before the
  step. Only a pre-condition inside another pre-condition is nested (`P1.1`).
- **One name per state.** Use the same title wherever the state is meant; put
  the code in one fixture and let tests request it.
- Like a step, a pre-condition gets a status, and an exception inside it is
  attached to it and raised again. Checks made inside it (`assert`,
  pytest-verifier) are its entries.
- Steps inside a pre-condition are numbered in it (`P1.2. Put the listeners in
  standby`, `P1.2.1`…).
