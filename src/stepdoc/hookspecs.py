"""Hooks stepdoc calls, so other plugins can follow steps without importing it (EXT-1).

Implement them with ``@pytest.hookimpl(optionalhook=True)``: pytest then accepts
them when stepdoc is not installed. They are only called while stepdoc records
(a stepdoc output option or ``--stepdoc-dry-run`` is passed).
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.hookspec
def pytest_stepdoc_step_started(item: pytest.Item, step: Any) -> None:
    """A step started in ``item``. ``step`` is a ``stepdoc.Step``: its ``number``,
    ``title``, ``section`` and ``location`` are set; its entries are still empty."""


@pytest.hookspec
def pytest_stepdoc_step_finished(item: pytest.Item, step: Any) -> None:
    """A step ended. ``step.status`` and ``step.error`` are set. A check another
    tool reports later (at the end of the test phase) can still change them."""


@pytest.hookspec
def pytest_stepdoc_test_doc(item: pytest.Item, doc: dict[str, str]) -> None:
    """The documentation of ``item``, from its test function's own docstring
    (DOC-1). Called once per test, before it runs (before any retry of it), and
    only for a test whose docstring has text. ``doc`` is plain JSON data with
    only the keys that have text:

    - ``"description"``: the text before the first heading (summary and paragraphs),
    - ``"objective"``: the ``Objective:`` section,
    - ``"acceptance"``: the ``Acceptance criteria:`` section (also ``Pass criteria:``).

    Each value is the section's text, dedented, as written: list markers
    (``1.``, ``-``) and backticks are kept. The same dict is the case's ``doc``
    in the run record."""


@pytest.hookspec
def pytest_stepdoc_record_ready(config: pytest.Config, record: dict[str, Any]) -> None:
    """The JSON run record of the session, before stepdoc writes any file. It is
    plain JSON data in the schema of ``stepdoc/schema/run-record.schema.json``.
    Read it, do not change it. Under pytest-xdist only the main process calls it."""
