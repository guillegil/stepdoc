# Test procedures

**Contents**

1. [test_dev](#tests-t-py-test_dev) · `tests/t.py::test_dev`
2. [test_api](#tests-t-py-test_api) · `tests/t.py::test_api`

<a id="tests-t-py-test_dev"></a>

## test_dev

_tests/t.py::test_dev · 2 cases_

**Parameters**

| Parameter | Values |
|---|---|
| `level` | `1`, `2` |

**Procedure for `1`**

- **1. Set `dac` level**

  > The DAC settles in 1 ms.
  - 1.1. `dev.map.dac.level = 1` _value from this run_
  - 1.2. Read `dev.map.adc.value`
- **2. Check**
  - 2.1. Verify `adc == 4`

**Procedure for `2`**

- **1. Set `dac` level**

  > The DAC settles in 1 ms.
  - 1.1. `dev.map.dac.level = 2` _value from this run_
  - 1.2. Read `dev.map.adc.value`
- **2. Check**
  - 2.1. Verify `adc == 4`

<a id="tests-t-py-test_api"></a>

## test_api

_tests/t.py::test_api · 1 case_

**Procedure**

- **1. Create <user> | *x***
  - 1.1. `/users  {'name': '<script>'}` _value from this run_
