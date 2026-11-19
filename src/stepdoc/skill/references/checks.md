# Checks

stepdoc records checks; it never compares values itself. A check is one of:

| Form | Example |
|---|---|
| Plain `assert` | `assert r.status_code == 201` |
| One-line step | `step("PLL locked", check=dev.map.status.pll_locked)` |
| Callable | `step("Queue empty", check=lambda: queue.size() == 0)` |
| `stepdoc.Check` | `stepdoc.attach_check(stepdoc.Check("Output in range", passed=ok, expected=1.65, actual=v))` |
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
`Verify ctl.tx == 0 (room for commands)`. Comments that start with a developer
marker (`TODO`, `FIXME`, `XXX`, `HACK`, `noqa`, `pragma`, `type:`, `fmt:`,
`pylint:`, `isort:`, `mypy:`, `pyright:`, `ruff:`; a marker after the note is cut
off: `# FIFO empty  # noqa`), empty comments, commented-out code and comments
indented deeper than the check are not notes. Write these comments for the reader of the procedure. Turn the
lookup off with `stepdoc_comment_notes = false`.

## pytest-verifier

With pytest-verifier installed, `verify.*` checks are recorded through stepdoc's
verify adapter: each becomes a "Verify …" line in the step it ran in. Don't wrap
them in `check=` or `attach_check`. Give the expected value as a name or an
expression (`verify.equal(r.json()["age"], age, name="Stored age")`) so the
procedure reads `== <age>` instead of one run's number. If the procedure shows
`?` steps, the adapter is off (`stepdoc_adapters`). Operand names, the words of
an `all_satisfy` line, `bits` and hex values need pytest-verifier 0.11 or newer;
with an older one the checks read as pytest-verifier describes them.

Comparisons (`equal`, `not_equal`, `approx`, `greater…`, `less…`, `between`),
`bits` and `is_true`, `is_false`, `is_none`, `is_not_none` start with the checked value. A
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
  `not_equal`, `approx`, `greater…`, `less…`, `length` and `bits`.
- HTML shows the checked expression, comparison and operand names in monospace;
  `name=` and notes stay prose.

### Registers: hex values and the bits that differ

pytest-verifier 0.11 shows integers in a format when the check has `radix=` or
`width=`, is a `verify.bits`, or checks a value whose class has
`__verify_format__`. The report then shows the value the check got in that
format, and for a failed `equal` or `bits` the bits (and named fields) that
differ:

```python
verify.equal(dev.CTRL.value, wdata, radix="hex", width=16)
verify.bits(dev.CTRL.value, wdata, mask=MASK, fields={"EN": 0, "MODE": (3, 1)}, width=16)
```

```text
procedure   Verify dev.CTRL == <wdata>
            Verify dev.CTRL & <MASK> == <wdata>
report      Verify dev.CTRL == 0xABF3 -> got 0x12F0, bit 15, bits [13:11], bit 8 and bits [1:0] differ ✗
            Verify dev.CTRL & 0x00FF == 0x00F3 -> got 0x12F0, bits [1:0] differ: EN [0] is 0b0, expected 0b1; MODE [3:1] is 0b000, expected 0b001 ✗
```

- Give the expected value and the mask as names (a parameter, a constant): the
  procedure then shows `<wdata>` and `<MASK>` and no value of the run. A literal
  is shown as pytest-verifier writes it (`& 0x00FF == 0x00F3`) when the call
  fixes the format: `radix=` and `width=` are literals where given, and the
  check has no width or radix the call does not give.
- When the format depends on the run (`width=width`, or the width a value's
  `__verify_format__` gives: `verify.equal(reg.read(), 0xF3)`), the procedure
  shows a literal as the test wrote it (`reg.read() == 0xF3`), so registers of
  different widths share one procedure. Write such literals the way they should
  read. A mask or expected value passed through `**kwargs` reads `<mask>`,
  `<expected>`.
- A checked value that binds more loosely than `&` is put in parentheses:
  `verify.bits(dev.CTRL.value | 1, wdata, mask=MASK)` reads `(dev.CTRL.value |
  1) & <MASK> == <wdata>`.
- In a loop, a `verify.*` check is one line per pass (in a plain loop and in
  `stepdoc.repeat`): its checks are placed after the phase, so they are not part
  of the collapsed "Repeat". Check once after the loop, or collect the values
  and use `verify.all_satisfy` (one line, below).
- The report shows the expected value under the mask (`0x00F3` for `0xABF3`
  under `0x00FF`), as pytest-verifier does. Beside a mask given by name, the
  procedure shows a literal expected value whole (`& <MASK> == 0xABF3`).
- `name=`, `actual_name=` and `expected_name=` work as on `equal`:
  `Verify 'Low byte': dev.CTRL & 0x00FF == <wdata>`.
- The procedure never shows the value got or what differs. A passed check shows
  its value (`-> got 0x12F0 ✓`) and no difference.
- Without a format the report shows the value as Python does (`-> got 240`).
  The line of a read is the bridge's, so a read on its own line stays
  `Read dev.CTRL -> 4848`; read on the check's line, it is folded into the check.
- pytest-verifier's `detail` text is not printed; it is in the run record.

### Other verify checks

- `contains`, `not_contains`, `length`, `matches` and `is_instance` keep the name
  in quotes, also an automatic one: `Verify 'items' contains <n>`, `Verify 's'
  matches /^v\d/`. Give them a `name=` that reads well.
- `conditional` and `guard` are one line with the branch taken and no line for
  the check inside: `Verify 'Mode' [mode=3] [→ 3]`.
- `eventually` and `stable` show the kept try's check as a child line.
- `raises` reads `Verify 'Bad word' raises ValueError`; `fail` reads `Verify
  FAIL: <msg>`; each row of `verify.limits` is a line of its own, named by the
  row (`Verify 'CTRL' & 0x00FF == 0x00F3`).
- `verify.section` titles are not shown: use `step()` for structure.

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
- A dry run says `-> 12 items`. A failed item's line shows its value as the
  check does (`item [1]: r == 0x01 -> got 0x02, bits [1:0] differ`).
- Run record: the line's check has `each` (`count`, `failed`, `item`, `items`,
  `values`: what each item's check got, the first and last 32 of more than 64;
  `texts`: the same values as the checks show them, when they have a format) and
  its `children` are the failed items, each with its `index`.

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
