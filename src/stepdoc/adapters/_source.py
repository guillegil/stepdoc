"""Source lookups the adapters share: which lines a step covers, and the call
that made a check. Parsed files and every lookup are cached per run, so a check
made in a loop costs one lookup per source line, not one per pass."""

from __future__ import annotations

import ast
import os
from functools import lru_cache
from typing import Optional

import executing

from ..core.symbolic import is_constant, secret_name


@lru_cache(maxsize=256)
def _tree(path: str) -> Optional[ast.Module]:
    """The parsed file, its nodes linked to their ``parent`` (executing's tree, so
    local aliases can be followed: :func:`stepdoc.core.symbolic.target_pattern`)."""
    try:
        tree = executing.Source.for_filename(path).tree
    except Exception:
        return None
    return tree if isinstance(tree, ast.Module) else None


@lru_cache(maxsize=256)
def _text(path: str) -> Optional[str]:
    try:
        text = executing.Source.for_filename(path).text
    except Exception:
        return None
    return text or None


@lru_cache(maxsize=1024)
def same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


@lru_cache(maxsize=4096)
def statement_span(path: str, line: int) -> tuple[int, int]:
    """Lines covered by the statement starting at ``line``: a whole ``with`` block
    for ``with step(...)``, the call itself for a decorated step's call. Falls
    back to the single line when the source cannot be read."""
    end = _statement_ends(path).get(line)
    if end is None:
        return line, line
    return end


@lru_cache(maxsize=256)
def _statement_ends(path: str) -> dict[int, tuple[int, int]]:
    """For each line a statement starts on (its own line, or its first
    decorator's), the span of the longest such statement: one walk of the file
    for every :func:`statement_span` lookup in it."""
    tree = _tree(path)
    if tree is None:
        return {}
    best: dict[int, ast.stmt] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        for line in {node.lineno, _first_line(node)}:
            held = best.get(line)
            if held is None or (node.end_lineno or line) > (held.end_lineno or line):
                best[line] = node
    return {
        line: (min(node.lineno, line), node.end_lineno or line) for line, node in best.items()
    }


def _first_line(node: ast.stmt) -> int:
    """Where a decorated function starts (its first decorator): the line its code
    object reports, which is where a ``@precondition`` function is located."""
    decorators = getattr(node, "decorator_list", None)
    return min((d.lineno for d in decorators), default=node.lineno) if decorators else node.lineno


@lru_cache(maxsize=4096)
def calls_at(path: str, line: int, method: str) -> tuple[ast.Call, ...]:
    """The calls of ``something.method(...)`` (or ``method(...)``) that span
    ``line`` and are not inside another such call, in source order: one per
    check made on the line (``verify.equal(a.equal(b), True)`` gives the outer
    call only; two checks on one line give two calls)."""
    found = [
        (call, span)
        for call, span in _calls_named(path, method)
        if span[0] <= line <= span[2]
    ]
    outer = [
        (c, span) for c, span in found
        if not any(o is not c and _within(span, o_span) for o, o_span in found)
    ]
    return tuple(c for c, _ in sorted(outer, key=lambda pair: pair[1]))


@lru_cache(maxsize=1024)
def _calls_named(path: str, method: str) -> tuple[tuple[ast.Call, tuple[int, int, int, int]], ...]:
    """Every call of ``method`` in the file with its extent: one walk of the file
    per method name, however many lines are looked up in it."""
    tree = _tree(path)
    if tree is None:
        return ()
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == method:
            found.append((node, _extent(node)))
    return tuple(found)


def _extent(node: ast.expr) -> tuple[int, int, int, int]:
    return (
        node.lineno,
        node.col_offset,
        node.end_lineno or node.lineno,
        node.end_col_offset if node.end_col_offset is not None else node.col_offset,
    )


def _within(inner: tuple[int, int, int, int], outer: tuple[int, int, int, int]) -> bool:
    return outer[:2] <= inner[:2] and inner[2:] <= outer[2:]


def argument(call: ast.Call, position: int, keyword: str) -> Optional[ast.expr]:
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return call.args[position] if len(call.args) > position else None


def source_text(path: str, node: ast.expr) -> Optional[str]:
    """``node``'s source as written, on one line."""
    text = _text(path)
    source = ast.get_source_segment(text, node) if text is not None else None
    return " ".join(source.split()) if source else None


def symbolic(path: str, node: ast.expr) -> Optional[str]:
    """``<source>`` for an expression that is not a literal, None for a literal
    and for a named constant (``MY_CONST``, ``consts.LIMIT``: the same in every
    run, so the procedure shows its value, SYM-9), unless its name says it is a
    secret."""
    try:
        ast.literal_eval(node)
        return None
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        pass
    if is_constant(node) and not secret_name(ast.unparse(node)):
        return None
    text = _text(path)
    source = ast.get_source_segment(text, node) if text is not None else None
    if not source:
        return None
    return f"<{' '.join(source.split())}>"
