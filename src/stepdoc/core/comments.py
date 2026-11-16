"""Notes from source comments (CHK-9): the comment a reader sees next to a check.

``comment_note(file, line)`` gives the note for the statement that spans
``line``: a trailing ``# ...`` comment on the statement's first line, else the
block of ``#`` comment lines directly above it (no blank line in between, joined
with spaces). Developer markers (``TODO``, ``noqa``, ``type:``…), empty comments
and commented-out code are not notes.

Files are tokenized and parsed once, and every ``(file, line)`` is answered once,
so this costs nothing per loop pass. It runs when a recorder resolves (after the
test), never while actions are recorded.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

__all__ = ["comment_note", "clear_caches"]

# A tool directive after the note in the same comment: ``# note  # noqa: E501``.
_TRAILING_MARKER = re.compile(
    r"\s#\s*(?:type\s*:|noqa\b|pragma\b|pylint\s*:|fmt\s*:|isort\s*:|mypy\s*:|pyright\s*:|ruff\s*:)",
    re.IGNORECASE,
)
_MARKERS = re.compile(
    r"(?:(?:todo|fixme|xxx|hack)\b|noqa\b|type\s*:|pragma\b|pylint\s*:|fmt\s*:|isort\s*:"
    r"|mypy\s*:|pyright\s*:|ruff\s*:|-\*-|!)",
    re.IGNORECASE,
)
# Statements a sentence never parses as: commented-out code.
_CODE = (ast.Assign, ast.AugAssign, ast.Import, ast.ImportFrom)
# Statements a short sentence may parse as (``return code``, ``pass``): code
# only with code punctuation in the text.
_KEYWORDS = (
    ast.Assert, ast.Return, ast.Raise, ast.Delete, ast.Pass, ast.Break, ast.Continue,
    ast.Global, ast.Nonlocal,
)
_PUNCTUATION = re.compile(r"[()=.\[\]]")


@dataclass
class _File:
    full: dict[int, tuple[int, str]] = field(default_factory=dict)
    """Line -> column and text of a comment that is alone on its line."""
    trailing: dict[int, str] = field(default_factory=dict)
    """Line -> text of a comment after code."""
    starts: list[tuple[int, int, int]] = field(default_factory=list)
    """``(first, last, column)`` of every simple statement."""


@lru_cache(maxsize=128)
def _scan(path: str) -> Optional[_File]:
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    out = _File()
    try:
        # PEP 263: a coding cookie (or a BOM) says how the file is encoded.
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        text = raw.decode(encoding)
        if text.startswith("\ufeff"):
            text = text[1:]
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type != tokenize.COMMENT:
                continue
            row, col = tok.start
            if tok.line[:col].strip():
                out.trailing[row] = tok.string
            else:
                out.full[row] = (col, tok.string)
        tree = ast.parse(text, path)
    except (SyntaxError, ValueError, UnicodeDecodeError, LookupError, tokenize.TokenError):
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt) and not hasattr(node, "body"):
            out.starts.append((node.lineno, node.end_lineno or node.lineno, node.col_offset))
    return out


def _clean(comment: str) -> Optional[str]:
    """The words of one comment, or None for a marker, an empty comment or code."""
    text = _TRAILING_MARKER.split(comment.lstrip("#"), maxsplit=1)[0].strip()
    if not text or _MARKERS.match(text):
        return None
    return None if _is_code(text) else text


def _is_code(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return False
    for stmt in tree.body:
        if isinstance(stmt, _CODE):
            return True
        if isinstance(stmt, ast.AnnAssign):
            # ``Expected: 1``, ``State: idle`` are labels; ``x: int = 1`` and
            # ``x: list[int]`` are code.
            if stmt.value is not None or not isinstance(stmt.annotation, (ast.Name, ast.Constant)):
                return True
            continue
        if isinstance(stmt, _KEYWORDS) and _PUNCTUATION.search(text):
            return True
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, (ast.Call, ast.Await)):
            return True
    return False


def _statement_start(f: _File, line: int) -> tuple[int, Optional[int]]:
    """First line and column of the innermost simple statement spanning ``line``
    (the column is None when no statement is known there)."""
    best: Optional[tuple[int, int, int]] = None
    for first, last, col in f.starts:
        if first <= line <= last and (best is None or last - first < best[1] - best[0]):
            best = (first, last, col)
    return (line, None) if best is None else (best[0], best[2])


@lru_cache(maxsize=4096)
def comment_note(path: str, line: int) -> Optional[str]:
    """The note a source comment gives the statement at ``path:line``, or None.
    Never raises."""
    try:
        f = _scan(path)
        if f is None:
            return None
        first, column = _statement_start(f, line)
        trailing = f.trailing.get(first)
        if trailing is not None:
            note = _clean(trailing)
            if note:
                return note
        block: list[str] = []
        above = first - 1
        # Only comments indented like the statement: one that ends a deeper
        # block above belongs to that block.
        while above in f.full and (column is None or f.full[above][0] == column):
            block.append(f.full[above][1])
            above -= 1
        words = [w for w in (_clean(c) for c in reversed(block)) if w]
        return " ".join(" ".join(words).split()) or None
    except Exception:  # pragma: no cover - defensive: documents never break a run
        return None


def clear_caches() -> None:
    """Forget scanned files (after they changed, or between benchmark rounds)."""
    _scan.cache_clear()
    comment_note.cache_clear()
