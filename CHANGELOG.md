# Changelog

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
