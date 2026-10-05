# Reviewing a procedure

Generate it after every change to a test:

```bash
pytest path/to/test_file.py --stepdoc-procedure=out/procedure.md
```

Then read it as a reviewer would, and fix the test, not the document.

| You see | Cause | Fix |
|---|---|---|
| `2048` where a parameter belongs | a literal in the test | use the parameter or a named variable |
| `(value from this run)` | the source line was not found | add the driver or client to `stepdoc_skip_modules`; set the bridge's `value_from` |
| `<some_long_expression>` | an inline computation | assign it to a well-named variable, or `stepdoc.value("name", expr)` |
| Actions under "Unscoped" | actions outside any `with step(...)` | wrap them in a step, or move them into a fixture |
| Dozens of identical reads | reads from different lines | read in one loop line, or use `@step(..., record=False)` |
| A step with no lines | the step's actions were not recorded | check the bridge is attached in the fixture the test uses |
| No "Verify" lines for passing asserts | `enable_assertion_pass_hook` is off | set it in the pytest ini and clear `.pyc` caches |
| Procedure split into variants | different parameters took different code paths | intended? if not, make the steps unconditional |
| Low-level noise from a helper | the helper records every register access | `@step("…", record=False)` on the helper |

## Good titles

- Imperative, short, specific: "Create user", "Wait for PLL lock".
- Not "Step 1", not the function name, not a sentence about the test.

## Setup and teardown

Steps run in fixtures appear under **Setup** (S1…) and **Teardown** (T1…). Put
bench preparation and cleanup there instead of in the test body.
