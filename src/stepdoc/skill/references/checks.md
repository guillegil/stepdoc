# Checks

stepdoc records checks; it never compares values itself. A check is one of:

| Form | Example |
|---|---|
| Plain `assert` | `assert r.status_code == 201` |
| One-line step | `step("PLL locked", check=dev.map.status.pll_locked)` |
| Callable | `step("Queue empty", check=lambda: queue.size() == 0)` |
| `stepdoc.Check` | `stepdoc.attach_check(Check("Output in range", passed=ok, expected=1.65, actual=v))` |
| Converted object | anything a `register_check_converter` predicate accepts |

## Plain assert

- Passing asserts become "Verify …" lines only with
  `enable_assertion_pass_hook = true` (and a clean `.pyc` cache after enabling it).
- A failing assert is attached to the step it ran in, with its message.
- A read on the same source line is shown on the check's line:
  `Verify dev.map.adc.value == 1024 -> got 1024`.

## step(check=...)

`step(title, check=value)` records a one-line step holding one check. It is not a
context manager and cannot decorate a function.

## Not judged

`Check(text, passed=None)` is recorded as documentation only and never turns a
step red or green. In dry-run every check is recorded this way.

## Status

A step is ✗ if any check or child step failed, `?` if something is unknown, ✓
otherwise. Exceptions mark the step as error and are re-raised.

## Converters

Tools with their own check objects register a converter instead of stepdoc
knowing about them:

```python
stepdoc.register_check_converter(
    lambda obj: isinstance(obj, MyVerdict),
    lambda obj: stepdoc.Check(obj.name, passed=obj.ok, expected=obj.expected, actual=obj.actual),
)
```

An object no converter understands raises `stepdoc.CheckTypeError` naming the
package it comes from.
