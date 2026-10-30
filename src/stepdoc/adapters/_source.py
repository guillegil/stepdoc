"""Source lookups the adapters share: which lines a step covers, and the call
that made a check. Parsed files are cached per run."""

from __future__ import annotations

import ast
import os
from functools import lru_cache
from typing import Optional


@lru_cache(maxsize=256)
def _tree(path: str) -> Optional[ast.Module]:
    try:
        with open(path, encoding="utf-8") as f:
            return ast.parse(f.read(), path)
    except (OSError, SyntaxError, ValueError):
        return None


@lru_cache(maxsize=256)
def _text(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def statement_span(path: str, line: int) -> tuple[int, int]:
    """Lines covered by the statement starting at ``line``: a whole ``with`` block
    for ``with step(...)``, the call itself for a decorated step's call. Falls
    back to the single line when the source cannot be read."""
    tree = _tree(path)
    if tree is None:
        return line, line
    best: Optional[ast.stmt] = None
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt) and node.lineno == line:
            if best is None or (node.end_lineno or line) > (best.end_lineno or line):
                best = node
    if best is None:
        return line, line
    return best.lineno, best.end_lineno or line


def call_at(path: str, line: int, method: str) -> Optional[ast.Call]:
    """The innermost call of ``something.method(...)`` (or ``method(...)``) that
    spans ``line``."""
    tree = _tree(path)
    if tree is None:
        return None
    found: Optional[ast.Call] = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != method or not (node.lineno <= line <= (node.end_lineno or node.lineno)):
            continue
        if found is None or node.lineno >= found.lineno:
            found = node
    return found


def argument(call: ast.Call, position: int, keyword: str) -> Optional[ast.expr]:
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return call.args[position] if len(call.args) > position else None


def symbolic(path: str, node: ast.expr) -> Optional[str]:
    """``<source>`` for an expression that is not a literal, None for a literal."""
    try:
        ast.literal_eval(node)
        return None
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        pass
    text = _text(path)
    source = ast.get_source_segment(text, node) if text is not None else None
    if not source:
        return None
    return f"<{' '.join(source.split())}>"
