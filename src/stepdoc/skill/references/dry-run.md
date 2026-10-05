# Dry-run: the procedure without the real system

`pytest --stepdoc-dry-run --stepdoc-procedure=out/procedure.md` runs the tests to
collect the procedure without judging results.

- Fixtures switch to a simulated backend through the session-scoped
  `stepdoc_dry_run` fixture (or `stepdoc.is_dry_run(config)`):

  ```python
  @pytest.fixture(scope="session")
  def api(stepdoc_dry_run):
      transport = fake_api_transport() if stepdoc_dry_run else None
      ...
  ```

- Every check is recorded as not judged. A failing assert inside a step is
  recorded and swallowed, so later steps are still documented.
- Without the option, `stepdoc_dry_run` is `False` and nothing changes.

## Known limits

- Python evaluates arguments immediately, so code before a step still runs.
- Branches the simulation does not take are not documented. If the procedure
  shows different variants for different parameters, the simulation decided the
  path.
- Values that come from the simulated system (reads, responses) are the
  simulation's values; the procedure shows them symbolically, the dry-run
  report concretely.
