# Changelog

## Unreleased

### Added
- Test descriptions from docstrings (DOC-1). A test function's own docstring is
  shown under the test's heading, before its parameters and steps, in the
  procedure (once per test, from the first case that has one) and the report
  (per case): the text before the first heading as the description, then
  `Objective:` (`**Objective.** text` when it is one paragraph) and `Acceptance
  criteria:` (also `Objectives`, `Pass criteria`, `Pass/Fail criteria`,
  `Acceptance/Pass criteria`, `criterion`; any case; text may follow the colon).
  A sentence stays a paragraph, `-`/`*` and `1.`/`1)` lines become lists, also
  when indented under an intro line or nested (the author's numbers kept,
  indented lines joined to the item above; a wrapped line starting with a number
  other than 1 stays in its paragraph), backticks show
  as code. Other lines ending in `:` stay text; a `Description:` heading is
  accepted. Class and module docstrings are not read, and a test without a
  docstring renders as before (HTML adds its `.doc` styles only to pages that
  show one). Run record schema 0.5: a case may have `doc` (`description`,
  `objective`, `acceptance`, only those with text, each the section's text as
  written). The `pytest_stepdoc_test_doc(item, doc)` hook gives other plugins the
  same dict once per test, before it runs.
- Pre-conditions: `stepdoc.precondition("System Mode A")`, a context manager
  and a decorator (PRE-1). A pre-condition is a named state the system must be
  in before the test, apart from the rest of the setup. What is recorded inside
  (actions, checks, steps) goes into a **Pre-conditions** section before Setup,
  numbered `P1`, `P1.1`…, in the procedure and the report (text, Markdown and
  HTML), whichever phase the block ran in. Like a step it gets a status, takes
  `note=`, and an exception is attached to it and raised again. A block written
  inside a setup or procedure step is not part of that step: it is the next
  pre-condition and the step goes on after it (PRE-2); only a pre-condition
  inside another one is nested. As a decorator it wraps the whole function; on
  a generator function (a fixture that yields, with `@pytest.fixture` above
  `@stepdoc.precondition(...)`) only the part before the first `yield`, so what
  runs after it is ordinary teardown. Coroutine functions and async generators
  are wrapped too. pytest-verifier checks made inside a pre-condition are
  placed in it (those a fixture makes after its `yield` stay in Teardown). A
  `record=False` step around a pre-condition does not hide its actions.
  `@stepdoc.precondition(...)` written above `@pytest.fixture` raises a
  `TypeError` that says to swap them.
- Pre-conditions already established (PRE-3). A test that uses a session,
  module or class fixture which applied a pre-condition for an earlier test
  lists it too, before its own, without entries: `P1. System Mode A   already
  established, applied in test_bus.py::test_send`, in the report with the
  status it got there, or `not established, failed in …` when it failed
  there. This covers a fixture reached through another fixture, through an
  override that asks for the fixture it overrides, and through
  `request.getfixturevalue` in the test; a test skipped before its fixtures
  were set up lists none. A parametrized test's procedure shows the pre-condition
  in full when one of its cases applied it. The
  `pytest_stepdoc_step_started`/`finished` hooks are called for these lines as
  well.
- Run record (schema 0.5): a step's `section` may be
  `precondition`, and a pre-condition the test did not apply itself has
  `applied_in` (the id of the test that applied it) and no entries.
  `Step.applied_in` carries it to plugins that follow steps.
- `verify.all_satisfy` reads as one line (CHK-11). pytest-verifier makes one
  check per item, which used to be one line per item (twelve times `Verify
  'Index error': w & INDEX_ERROR_BIT == 0`). When every item's check reads the
  same, the procedure now shows `Verify 'Index error clear': for each w in
  waveforms, w & INDEX_ERROR_BIT == 0`, and the report adds `12 of 12 passed`,
  or `2 of 12 failed` with one line per failed item (`item [3]: ... -> got 64
  ✗`); a dry run says `12 items`. The words `w` and `waveforms` come from
  pytest-verifier 0.11's `item_name` and `items_name` (`for each item, ...`
  without them). The line carries the `all_satisfy`'s name, not the items'
  `name=`; a note all items share moves to it. Items checked in different ways,
  composite items and an `all_satisfy` that could not check every item stay
  listed as before. Run record: a check may have `each` (`count`, `failed`,
  `item`, `items`, `values`: what each item's check got, the first and last 32
  when there are more) and a failed item `index`; `Check.each` (`stepdoc.Each`)
  and `Check.index` for other plugins.
- Operand names word verify checks. A pytest-verifier check made with
  `actual_name=` reads with that name in place of the folded register read or the
  argument's source, and one made with `expected_name=` names its expected value:
  the procedure shows only the generic form (`Verify
  tb.dev.map.block.reg<channel>.F == <wdata>`: each interpolation as `<...>`, a
  plain name as written, the expected name in `<...>` like any expected value
  given by name, no value of the run), the report this run's
  (`tb.dev.map.block.reg3.F == wdata (243) -> got 240 ✗`). With `name=` too, the
  name comes first (`'Stored word': tb.dev.map.block.reg<channel>.F == <wdata>`).
  All 17 value checks take `actual_name=`; the comparisons and `length` take
  `expected_name=`, with units and `approx` tolerances kept (`== <nominal> V ±
  0.05V`). Reads on the check's line still go into the check, and composite
  children are worded the same way. The procedure's text is built from the
  names themselves, so it holds no value of the run even when pytest-verifier's
  description shortens or escapes a name. An expected name whose `<` and `>`
  compare (`a < b > c`) is no variable part and gets its `<...>` too. Line
  breaks and control characters in a name read as a space. Two checks of one
  kind on one line are told apart by their `actual_name=` (a string, or an
  f-string whose literal parts fit only one of them), so each takes its own read
  and expected name. Records without the names read exactly as before.
- Run record schema 0.4: checks get an optional `operands` (`actual` and
  `expected`, each `{"concrete", "symbolic"}`) and an optional `label` (the
  `name=` the text starts with). `Check.operands` and `Check.label` carry them to
  plugins that follow steps.
- HTML: the code of a verify line is `<code class="expr">`; a name given with
  `name=` is shown before it in the text font (`<span class="label">`), as the
  note already is after it. Code uses a fuller system monospace stack
  (`ui-monospace`, SF Mono, Menlo, Cascadia Mono, Consolas, Roboto Mono, DejaVu
  Sans Mono, Liberation Mono; no web fonts) without ligatures, so `==`, `!=` and
  `<=` stay as typed; long dotted names may wrap after their dots and keep their
  background on every line, and a long `name=` without spaces wraps too. Markdown shows the name as words and the code between
  backticks, with a fence longer than any backticks inside.
- Verify lines read like the code. pytest-verifier's comparisons (`equal`,
  `not_equal`, `approx`, `greater`, `greater_equal`, `less`, `less_equal`),
  `between`, `is_true`, `is_false`, `is_none` and `is_not_none` start with the
  checked value: a register read made on the check's line is folded into the check
  (as for `assert`, ACT-5) and names it, its variable parts symbolic in the
  procedure (`Verify dev.block.esmi<channel>_32dat.txfe == 1`) and concrete in the
  report (`dev.block.esmi3_32dat.txfe == 1 -> got 1 ✓`); without a read, the
  argument's source as written, aliases not followed (`Verify write_count == 500`).
  A check only takes a read of its own pass (a polled read feeds as many checks
  as it stands for), so a loop over channels names each channel's register; a
  bare variable bound to a single read is named after that read. Two checks of
  one kind on one line each take their own argument and reads (told apart by the
  automatic name; named ones keep the old text). A check made without `name=`
  (descriptor `auto_name`) reads `<value> <op> <expected>`, one with a name
  `'<name>': <value> <op> <expected>`. A literal checked value, or a call site
  whose source cannot be read, keeps the old text (`'HTTP status' == 201`).
- Check notes: `Check.note`, shown after the check's text in parentheses in the
  procedure and the report (`Verify … == 1 (CMD FIFO empty)`; Markdown escapes it,
  HTML shows it as text). The verify adapter takes pytest-verifier's `note`; without
  one, and for plain `assert`s, the note is the comment at the end of the check's
  first line, else the comment lines directly above it, indented like the check.
  Markers (`TODO`, `FIXME`, `XXX`, `HACK`, `noqa`, `type:`, `pragma`, `pylint:`,
  `fmt:`, `isort:`), also a directive after the note (`# note  # noqa: E501`),
  empty comments and commented-out code are skipped; labels (`# Expected: 1`) and
  keyword-led prose (`# return code`) are notes. Files are decoded by their PEP 263
  coding cookie. Comments are read at the end of each test phase, once per file
  and line, so pytest-reporter's `procedure.json` has them. The `stepdoc_comment_notes` ini setting (default `true`)
  turns this off.
- Run record schema 0.4: checks get an optional `note`.
- `bench/bench_verify_text.py`: cost of recording, placing and resolving 10,000
  verify checks, with comment notes on and off.
- Variable parts of a target stay symbolic: a register reached with
  `getattr(obj, f"reg{ch}")` or `obj[i]`, directly or through a local alias
  (`reg = getattr(...)`, then `reg.value = val`), reads `dut.block.reg<ch>.value = <val>`
  in the procedure, so parametrized runs share one procedure. The bridge's concrete
  target is matched segment by segment from the right; when it does not match, the
  target is shown as before.
- Monospace in step titles: text between backticks is a code span, as in Markdown
  (``step("Write into `dut.reg1 = 1`")``). `stepdoc.fmt.mono(text)` writes a span for
  any text (it picks a fence longer than any backticks inside), so it works in
  f-strings, and `stepdoc.fmt.spans(title)` splits a title for renderers. The HTML
  outputs show the spans as `<code>`; Markdown and text keep the title as written.
  Titles stay plain strings in the run record.
- A bare `step("title")` statement records a one-line step with no check: `1.2`
  inside an open step, a top-level step outside one. Event listeners (a register
  tracer's subscriber, for example) can log what happened as steps, as
  pytest-reporter's old `step()`/`substep()` did. `with step(...)`, `@step(...)`
  and `step(..., check=...)` are unchanged, and a `step(...)` kept in a variable
  for a later `with` records nothing by itself.

- Loops collapse: when the same group of actions (up to 8) comes back to back, as
  the body of a `for` or `while` loop does, the passes become one `Repeat` entry
  holding one pass, with the number of passes in the report (`Repeat   16 times, 3 ms`).
  Passing checks made in every pass are part of it; a pass that differs (a failed
  check, another value) ends the loop and is shown as it ran.
- `with stepdoc.repeat("Drain the FIFO", until="FIFO is empty"):` around a loop
  says what it does and what it waits for, takes the loop's last test of its
  condition into the loop, and keeps actions before and after the block out of it.
  With a title the block is a step, at the top level or inside another step
  (`2. Drain the FIFO (repeat until FIFO is empty)`, its entries one pass, the
  report adding `✓ 16 times`); without one it is a `Repeat until …` entry.
  `counter="write_count"` names the variable that counts the passes
  (`…, counting write_count`) and `inc=4` how much it grows each pass
  (`write_count +4 each pass`). Everything after the title is keyword-only.
- `note=` on `step(...)` and `repeat(...)`: a sentence for the reader, shown under
  the line in the procedure and the report (Markdown as a quote, HTML in italics).
  Triple-quoted notes are dedented; a blank note is dropped.
- Run record schema 0.4: `repeat` entries (`until`, `count`, `t`, `t_last`,
  `entries`, optional `counter`, `inc`, `note`); steps get optional `note` and
  `loop` (`until`, `count`, `t_last`, optional `counter`, `inc`). Records written
  with 0.1 to 0.3 still render.

### Changed
- Markdown: the note after a line (a step's error, `value from this run`, the
  test that applied a pre-condition) is escaped, so `_`, `*`, `[`, `<` in an
  error message or a test id read as typed instead of formatting the line.
- A pytest-verifier check with a name and a non-literal checked value now reads
  `'Stored age': user["age"] == <age>` instead of `'Stored age' == <age>`.
- Placing pytest-verifier's checks after a phase no longer grows with the square
  of the number of steps and checks: 10,000 checks in 1,000 steps take about
  0.2 s instead of minutes. Source lookups are cached per line.
- Passing asserts in stepdoc's own code are no longer recorded as checks (pytest
  rewrites an editable plugin's asserts; recording them recursed).
- Passing asserts no longer keep a trailing comment in their text (some pytest
  versions include it); it becomes the check's note.
- The same write repeated from one source line, with the same value, collapses
  into one line, like polling: `Repeat dev.fifo.fifo_data = <cmd>`, and in the
  report `(16 times, 2 ms)`. Writes used to stay one line each.
  `stepdoc_collapse_repeats = false` keeps every write.
- Python 3.9 and 3.10 are supported (was 3.11+). CI tests 3.9 to 3.14.

## 0.2.0

First release on PyPI.

### Steps and recording
- Steps run by fixtures go into **Setup** (S1…) and **Teardown** (T1…) sections.
- Polling (the same read or request from one source line, repeated) collapses
  into one "Poll …" line with the count and duration. `stepdoc_collapse_repeats`
  turns it off.
- A read on a check's line is shown on the check: `Verify … -> got 1024`.
- `step(..., record=False)` keeps a helper's step but drops its low-level actions.
- `stepdoc.value(name, value)` names a value when the source can't.

### Outputs
- HTML output: one self-contained file, light and dark, printable.
- A contents list with test ids when a document holds several tests.
- `stepdoc render RECORD`: documents from a saved run record, without pytest.
- The output format follows the file extension (`.md`, `.html`, `.txt`).

### Integrations
- pytest-xdist: workers record, the main process writes one record and one set
  of documents.
- pytest-verifier adapter: `verify.*` checks become "Verify …" lines in their
  steps, with symbolic expected values in the procedure.
- pytest-strategies adapter: seed and row in the report, parameter domains in
  the procedure.
- A judging plugin without its adapter shows `?` steps, never a false ✓.
- `stepdoc_adapters` ini option and a `stepdoc.adapters` entry-point group.
- Hooks for other plugins: `pytest_stepdoc_step_started`,
  `pytest_stepdoc_step_finished`, `pytest_stepdoc_record_ready`.

### Agent skill
- `stepdoc skill install|status|uninstall` installs a skill that teaches AI
  coding agents to write tests with stepdoc.

### Run record
- Schema 0.3. Records written with 0.1 and 0.2 still render.

## 0.1.0 (not published)

- `step()` as context manager, decorator and one-line check; nested numbering.
- `record_action()` for bridges, with symbolic values found in the source.
- Plain `assert` recorded as checks; dry-run with a simulated backend.
- JSON run record, Markdown procedure and report, redaction of secrets.
