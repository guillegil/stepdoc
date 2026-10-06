# A complete example

A device bridge and a parametrized test. This example is run in stepdoc's own CI,
so it stays correct.

```python
# mydevice.py
class Device:
    """A tiny driver: every register write is an event."""

    def __init__(self):
        self.regs = {}
        self.hooks = []

    def write(self, reg, value):
        self.regs[reg] = value
        for hook in self.hooks:
            hook(reg, value)
```

```python
# conftest.py
import pytest
import stepdoc
from mydevice import Device


@pytest.fixture
def dev(stepdoc_dry_run):
    d = Device()
    d.hooks.append(lambda reg, val: stepdoc.record_action(reg, "write", val, value_from=("value",)))
    return d
```

```python
# test_levels.py
import pytest
from stepdoc import step


@pytest.mark.parametrize("level", [10, 20])
def test_levels(dev, level):
    with step("Set level"):
        dev.write("dac.level", level * 2)
    with step("Enable output"):
        dev.write("ctrl.enable", 1)
        assert dev.regs["ctrl.enable"] == 1
```

```ini
# pytest.ini
[pytest]
stepdoc_skip_modules = mydevice
enable_assertion_pass_hook = true
```

`pytest --stepdoc-procedure=procedure.txt` writes:

```text
test_levels.py::test_levels
Parameters:
  level   10, 20
1. Set level
   1.1. dac.level = <level * 2>
2. Enable output
   2.1. ctrl.enable = 1
   2.2. Verify dev.regs["ctrl.enable"] == 1
```
