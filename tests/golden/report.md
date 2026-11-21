# Test report

| Test | Outcome | Steps |
|---|---|---|
| [`tests/t.py::test_dev[1]`](#tests-t-py-test_dev-1) | PASSED | ✓ |
| [`tests/t.py::test_dev[2]`](#tests-t-py-test_dev-2) | FAILED | ✗ |
| [`tests/t.py::test_api`](#tests-t-py-test_api) | PASSED | ✓ |

<a id="tests-t-py-test_dev-1"></a>

## test_dev[1] — PASSED

_tests/t.py::test_dev[1] · 2026-01-01T00:00:00+00:00 · 12 ms_

Parameters: `level = 1`

- **1. Set `dac` level** ✓ _1 ms_

  > The DAC settles in 1 ms.
  - 1.1. `dev.map.dac.level = 1`
  - 1.2. Read `dev.map.adc.value` → `4`
- **2. Check** ✓ _1 ms_
  - 2.1. Verify `adc == 4` → `got 4` ✓

<a id="tests-t-py-test_dev-2"></a>

## test_dev[2] — FAILED

_tests/t.py::test_dev[2] · 2026-01-01T00:00:00+00:00 · 12 ms_

Parameters: `level = 2`

- **1. Set `dac` level** ✓ _1 ms_

  > The DAC settles in 1 ms.
  - 1.1. `dev.map.dac.level = 2`
  - 1.2. Read `dev.map.adc.value` → `8`
- **2. Check** ✗ _1 ms_
  - 2.1. Verify `adc == 4` → `got 8` ✗

<a id="tests-t-py-test_api"></a>

## test_api — PASSED

_tests/t.py::test_api · 2026-01-01T00:00:00+00:00 · 12 ms_

- **1. Create <user> | *x*** ✓ _1 ms_
  - 1.1. `/users  {'name': '<script>'}` → `201`
