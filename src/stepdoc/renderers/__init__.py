"""Renderers turn a JSON run record into documents. They read only the record
(D6), so the same output comes from pytest and from ``stepdoc render``."""

from __future__ import annotations

import os
from typing import Any, Callable, Literal

from . import html, markdown, text

Doc = Literal["procedure", "report"]
Renderer = Callable[[dict[str, Any]], str]

FORMATS: dict[str, dict[str, Renderer]] = {
    "md": {"procedure": markdown.render_procedure, "report": markdown.render_report},
    "html": {"procedure": html.render_procedure, "report": html.render_report},
    "text": {"procedure": text.render_procedure, "report": text.render_report},
}

_EXTENSIONS = {".md": "md", ".markdown": "md", ".html": "html", ".htm": "html", ".txt": "text"}


def format_for_path(path: str, default: str = "md") -> str:
    """The format a file name asks for (``report.html`` → ``html``)."""
    return _EXTENSIONS.get(os.path.splitext(path)[1].lower(), default)


def render(record: dict[str, Any], doc: Doc, fmt: str = "md") -> str:
    try:
        renderer = FORMATS[fmt][doc]
    except KeyError:
        raise ValueError(f"unknown format {fmt!r}; choose from {', '.join(FORMATS)}") from None
    return renderer(record)
