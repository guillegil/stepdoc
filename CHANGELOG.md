# Changelog

## Unreleased

### Added
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
