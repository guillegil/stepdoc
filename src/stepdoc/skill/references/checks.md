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

## Notes from comments

A check's note is shown after its text in parentheses, in the procedure and the
report. Without a `note=`, it is the comment at the end of the check's first line,
else the block of comment lines directly above it (no blank line in between):

```python
# CMD FIFO empty
verify.equal(dat.txfe, 1)
assert ctl.tx == 0  # room for commands
```

reads `Verify dev.block.esmi<channel>_32dat.txfe == 1 (CMD FIFO empty)` and
`Verify ctl.tx == 0 (room for commands)`. `TODO`, `FIXME`, `noqa`, `type:`,
`pragma`, `fmt:` and similar markers (also after the note: `# FIFO empty  # noqa`),
empty comments, commented-out code and comments indented deeper than the check
are not notes. Write these comments for the reader of the procedure. Turn the
lookup off with `stepdoc_comment_notes = false`.

## pytest-verifier

With pytest-verifier installed, `verify.*` checks are recorded through stepdoc's
verify adapter: each becomes a "Verify …" line in the step it ran in. Don't wrap
them in `check=` or `attach_check`. Give the expected value as a name or an
expression (`verify.equal(r.json()["age"], age, name="Stored age")`) so the
procedure reads `== <age>` instead of one run's number. If the procedure shows
`?` steps, the adapter is off (`stepdoc_adapters`).

Comparisons (`equal`, `not_equal`, `approx`, `greater…`, `less…`, `between`) and
`is_true`, `is_false`, `is_none`, `is_not_none` start with the checked value. A
register read on the check's line is folded into it and named by its target,
with the variable parts of a `getattr(..., f"reg{ch}")` alias kept symbolic:

```python
dat = getattr(tb.dev.block, f"esmi{channel}_32dat")
with step("Check the FIFO"):
    verify.equal(dat.txfe, 1)
    verify.equal(dat.tx8e, 1, note="at least 8 empty positions")
    verify.equal(write_count, 500, name="Filled")
```

```text
1. Check the FIFO
   1.1. Verify dev.block.esmi<channel>_32dat.txfe == 1
   1.2. Verify dev.block.esmi<channel>_32dat.tx8e == 1 (at least 8 empty positions)
   1.3. Verify 'Filled': write_count == 500
```

The report shows the concrete target and value (`dev.block.esmi3_32dat.txfe == 1
-> got 1 ✓`). Leave out `name=` when the expression says it all; give one when a
word helps. A literal checked value keeps the old form (`'HTTP status' == 201`).

A check is named after a read only when that read is its own: in a loop each
pass's check takes its pass's read (a polled read stands for as many checks as
reads). Otherwise it shows its argument as written, without following aliases
(`v = getattr(...).txfe` then `verify.equal(v, 1)` in a loop reads `Verify v ==
1` after the `Read … -> <v>` lines). Two checks of one kind on one line are told
apart by their automatic names or their `actual_name=`; ones with only `name=`
keep the old form there.

### Operand names

When the code does not say what a value is (a field read through `getattr`, a
value computed earlier), name the operands. The names word the check instead of
the source:

```python
block_reg_f = getattr(tb.dev.map.block, f"reg{channel}").F
verify.equal(block_reg_f, wdata,
             actual_name=f"tb.dev.map.block.reg{channel}.F", expected_name="wdata")
```

```text
procedure   Verify tb.dev.map.block.reg<channel>.F == <wdata>
report      Verify tb.dev.map.block.reg3.F == wdata (243) -> got 240 ✗
```

- Write `actual_name=` as an f-string (or a t-string on Python 3.14+) on the
  call itself, so the procedure gets the generic form (`reg<channel>`). A plain
  string, or one built elsewhere, is shown as given in both documents: with a
  run's value in it, parametrized cases stop sharing one procedure.
- The procedure never shows a value of the run for a named operand; the report
  shows the expected value once, after its name.
- An expected name reads `<wdata>` in the procedure; one with parts of its own
  (`limits[<channel>]`, from `f"limits[{channel}]"` or written so) stays as it is.
- Two checks of one kind on one line are told apart by their `actual_name=`.
  When the names fit both calls (`f"R{a}"` and `f"R{b}"`), each check keeps
  pytest-verifier's text and the reads stay on their own lines: put the checks
  on separate lines.
- `name=` is still allowed: `Verify 'Stored word': tb.dev.map.block.reg<channel>.F == <wdata>`.
- `actual_name=` is on every check of one value; `expected_name=` on `equal`,
  `not_equal`, `approx`, `greater…`, `less…` and `length`.
- HTML shows the checked expression, comparison and operand names in monospace;
  `name=` and notes stay prose.

## One check per item: `verify.all_satisfy`

```python
verify.all_satisfy(waveforms, lambda w: verify.equal(w & INDEX_ERROR_BIT, 0), name="Index error clear")
```

- Procedure: `Verify 'Index error clear': for each w in waveforms, w & INDEX_ERROR_BIT == 0`
  (one line, not one per item). Report: `-> 12 of 12 passed`, or `-> 2 of 12 failed`
  followed by one `item [i]: ... -> got ...` line per failed item.
- Pass the items as a named variable and use a lambda: its parameter and the
  variable are the words of the line (`for each item, ...` without them).
- Leave `name=` off the item's check: the line uses the `all_satisfy`'s name.
- Items checked differently (other check type, name or expression) stay one line each.

## step(check=...)

`step(title, check=value)` records a one-line step holding one check. It is not a
context manager and cannot decorate a function.

A bare `step(title)` statement, without `check=`, records a one-line step with no
check (passed unless in dry-run).

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
