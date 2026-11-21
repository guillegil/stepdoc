"""A test's docstring as its description, objective and acceptance criteria (DOC-1).

The text before the first recognized heading is the description (a PEP 257
summary line and paragraphs). Two headings start a section of their own, case
insensitive, at the start of a line and ending in a colon; text may follow the
colon on the same line::

    Objective:            (also Objectives)
    Acceptance criteria:  (also Pass criteria, Pass/Fail criteria,
                           Acceptance/Pass criteria; criterion too)

A ``Description:`` heading is accepted too. Any other line ending in a colon is
prose of the section it is in: nothing is dropped. Each section is dedented and
kept as written (list markers, backticks), so renderers decide how to show it.
"""

from __future__ import annotations

import inspect
import re
import textwrap
from typing import Optional

__all__ = ["parse_docstring", "SECTIONS"]

SECTIONS = ("description", "objective", "acceptance")
"""The keys of a parsed docstring, in the order documents show them."""

_HEADING = re.compile(
    r"(?P<name>description|objectives?"
    r"|(?:acceptance|pass)(?:\s*/\s*(?:acceptance|pass|fail))?\s+criteri(?:a|on))"
    r"\s*:(?P<rest>.*)",
    re.IGNORECASE,
)


def _section(name: str) -> str:
    name = name.lower()
    if name.startswith("description"):
        return "description"
    if name.startswith("objective"):
        return "objective"
    return "acceptance"


def _block(lines: list[str]) -> str:
    """Lines of one section without the indentation they share, trimmed."""
    text = textwrap.dedent("\n".join(lines))
    return "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")


def parse_docstring(text: Optional[str]) -> Optional[dict[str, str]]:
    """``{"description", "objective", "acceptance"}`` from a docstring, only the
    keys with text; ``None`` when there is no text at all."""
    if not text:
        return None
    text = inspect.cleandoc(text.replace("\r\n", "\n").replace("\r", "\n"))
    parts: dict[str, list[str]] = {}
    current = "description"
    first: dict[str, str] = {}  # text after a heading's colon, before its block
    lines: list[str] = []

    def close() -> None:
        body = _block(lines)
        head = first.pop(current, "")
        joined = "\n".join(p for p in (head, body) if p)
        if joined:
            parts.setdefault(current, []).append(joined)

    for line in text.split("\n"):
        m = _HEADING.fullmatch(line.rstrip()) if line[:1].strip() else None
        if m is None:
            lines.append(line)
            continue
        close()
        current, lines = _section(m.group("name")), []
        rest = m.group("rest").strip()
        if rest:
            first[current] = rest
    close()
    doc = {key: "\n\n".join(parts[key]) for key in SECTIONS if key in parts}
    return doc or None
