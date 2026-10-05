"""Symbolic value resolution (SYM-1…3, 5, 6, 7).

Two phases, so the hot path stays cheap:

1. **Capture** (event time, :func:`capture`): walk outward from the bridge to the
   first frame that is not in a skipped module and keep only
   ``(code, lasti, lineno, globals)`` plus the callee's code object. No AST work.
2. **Resolve** (render time, :meth:`Site.resolution`): ask ``executing`` which AST
   node that instruction belongs to and turn it into symbolic text. Results are
   cached per code location, so each source location is analysed once.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass, field
from types import CodeType, FrameType
from typing import Any, Callable, Optional, Sequence, TypeVar, Union

import executing

from .redaction import MASK, is_secret_key

__all__ = [
    "Site",
    "Resolution",
    "Sym",
    "capture",
    "look_through",
    "set_skip_modules",
    "skip_modules",
    "clear_caches",
    "assert_source",
]

F = TypeVar("F", bound=Callable[..., Any])
Selector = Union[str, int]

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

_ALWAYS_SKIPPED = ("stepdoc",)
_skip_prefixes: tuple[str, ...] = _ALWAYS_SKIPPED
_skip_by_code: dict[CodeType, bool] = {}
_look_through: set[CodeType] = set()


def set_skip_modules(prefixes: Sequence[str]) -> None:
    """Module prefixes whose frames the stack walk skips (``stepdoc_skip_modules``, SYM-2)."""
    global _skip_prefixes
    _skip_prefixes = _ALWAYS_SKIPPED + tuple(p for p in prefixes if p not in _ALWAYS_SKIPPED)
    _skip_by_code.clear()


def skip_modules() -> tuple[str, ...]:
    return _skip_prefixes


def look_through(fn: F) -> F:
    """Mark a helper so values that come from its parameters resolve to the
    caller's argument expression (SYM-6 prototype)."""
    code = getattr(fn, "__code__", None)
    if code is not None:
        _look_through.add(code)
    return fn


def _is_skipped(frame: FrameType) -> bool:
    code = frame.f_code
    skipped = _skip_by_code.get(code)
    if skipped is None:
        name = frame.f_globals.get("__name__") or ""
        skipped = any(name == p or name.startswith(p + ".") for p in _skip_prefixes)
        _skip_by_code[code] = skipped
    return skipped


# --------------------------------------------------------------------------- #
# Capture (hot path)
# --------------------------------------------------------------------------- #


class Site:
    """A code location captured at event time. Cheap to create, resolved later."""

    __slots__ = ("code", "lasti", "lineno", "globals", "callee", "outer")

    def __init__(
        self,
        code: CodeType,
        lasti: int,
        lineno: int,
        globals_: dict[str, Any],
        callee: Optional[CodeType],
        outer: Optional["Site"],
    ) -> None:
        self.code = code
        self.lasti = lasti
        self.lineno = lineno
        self.globals = globals_
        self.callee = callee
        self.outer = outer

    @property
    def filename(self) -> str:
        return self.code.co_filename

    # executing only reads these four attributes from a frame.
    @property
    def f_code(self) -> CodeType:
        return self.code

    @property
    def f_lasti(self) -> int:
        return self.lasti

    @property
    def f_lineno(self) -> int:
        return self.lineno

    @property
    def f_globals(self) -> dict[str, Any]:
        return self.globals

    def resolution(self) -> "Resolution":
        key = (self.code, self.lasti, self.callee)
        base = _resolutions.get(key)
        if base is None:
            base = _analyse(self)
            _resolutions[key] = base
        if self.outer is None:
            return base
        return base.with_outer(self.outer.resolution())


def capture(skip_frames: int = 1) -> Optional[Site]:
    """Capture the first user-code frame above the caller.

    ``skip_frames`` counts frames above :func:`capture`'s caller to ignore
    unconditionally (the bridge function). Frames in skipped modules are
    then walked over.
    """
    try:
        inner = sys._getframe(1 + skip_frames)
    except ValueError:
        return None
    frame = inner.f_back
    known = _skip_by_code.get
    while frame is not None:
        skipped = known(frame.f_code)
        if skipped is None:
            skipped = _is_skipped(frame)
        if not skipped:
            break
        inner = frame
        frame = frame.f_back
    if frame is None:
        return None
    return _site(frame, inner.f_code)


def _site(frame: FrameType, callee: Optional[CodeType]) -> Site:
    code = frame.f_code
    outer = None
    if code in _look_through:
        caller = frame.f_back
        inner = frame
        while caller is not None and _is_skipped(caller):
            inner = caller
            caller = caller.f_back
        if caller is not None:
            outer = _site(caller, inner.f_code)
    return Site(code, frame.f_lasti, frame.f_lineno, frame.f_globals, callee, outer)


# --------------------------------------------------------------------------- #
# Resolution (render time, cached per location)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Sym:
    text: str
    """Rendered for a value position: ``<width>``, ``100``, ``"Ana"``, ``{"name": <name>}``."""
    raw: str
    """Rendered inside a template: same as ``text`` but strings are unquoted."""
    source: str
    """Plain source of the expression: ``width``."""
    literal: bool


@dataclass
class Resolution:
    found: bool
    kind: str = "none"  # assign | call | read | other | none
    value_node: Optional[ast.expr] = None
    args: list[ast.expr] = field(default_factory=list)
    kwargs: dict[str, ast.expr] = field(default_factory=dict)
    param_names: tuple[str, ...] = ()
    target_source: Optional[str] = None
    bound_to: Optional[str] = None
    helper_params: frozenset[str] = frozenset()
    outer: Optional["Resolution"] = None
    note: Optional[str] = None
    _rendered: dict[Any, Optional[Sym]] = field(default_factory=dict, repr=False)

    def with_outer(self, outer: "Resolution") -> "Resolution":
        return Resolution(**{**self.__dict__, "outer": outer, "_rendered": {}})

    def node_for(self, value_from: Optional[Sequence[Selector]]) -> Optional[ast.expr]:
        if self.kind == "assign":
            return self.value_node
        if value_from is None:
            if self.kind == "call" and len(self.args) + len(self.kwargs) == 1:
                return self.args[0] if self.args else next(iter(self.kwargs.values()))
            return None
        for sel in value_from:
            if isinstance(sel, int):
                if 0 <= sel < len(self.args):
                    return self.args[sel]
                continue
            if sel in self.kwargs:
                return self.kwargs[sel]
            if sel in self.param_names:
                i = self.param_names.index(sel)
                if i < len(self.args):
                    return self.args[i]
        return None

    def select_value(self, value_from: Optional[Sequence[Selector]] = None) -> Optional[Sym]:
        key = None if value_from is None else tuple(value_from)
        try:
            return self._rendered[key]
        except KeyError:
            pass
        node = self.node_for(value_from)
        sym = None if node is None else render(self._expand(node))
        self._rendered[key] = sym
        return sym

    def _expand(self, node: ast.expr) -> ast.expr:
        """Look through a helper: replace its parameters with the caller's
        argument expressions, recursively up the chain of look-through helpers."""
        outer = self.outer
        if not self.helper_params or outer is None or not outer.found:
            return node
        params = self.helper_params

        class Substitute(ast.NodeTransformer):
            def visit_Name(self, n: ast.Name) -> ast.AST:
                if isinstance(n.ctx, ast.Load) and n.id in params:
                    rep = outer.node_for((n.id,))  # type: ignore[union-attr]
                    if rep is not None:
                        return outer._expand(_fresh(rep))  # type: ignore[union-attr]
                return n

        result: ast.expr = Substitute().visit(_fresh(node))
        return result


def _fresh(node: ast.expr) -> ast.expr:
    """Detached copy (executing's nodes carry ``.parent`` links; deepcopy would copy the tree)."""
    return ast.parse(ast.unparse(node), mode="eval").body


_resolutions: dict[tuple[CodeType, int, Optional[CodeType]], Resolution] = {}


def clear_caches() -> None:
    """Forget every analysed location, including ``executing``'s own caches (benchmarks)."""
    _resolutions.clear()
    _skip_by_code.clear()
    for name, value in list(vars(executing.Source).items()):
        if name.startswith("__") and "cache" in name and isinstance(value, dict):
            value.clear()


def _analyse(site: Site) -> Resolution:
    try:
        ex = executing.Source.executing(site)  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover - defensive
        return Resolution(found=False, note=f"executing failed: {exc!r}")
    node = ex.node
    note = None
    if node is None:
        node = _statement_fallback(ex.statements)
        if node is None:
            return Resolution(found=False, note="source or node not found")
        note = "statement fallback"
    res = _classify(node, site.callee)
    res.note = note
    if site.code in _look_through:
        code = site.code
        n = code.co_argcount + code.co_kwonlyargcount
        res.helper_params = frozenset(code.co_varnames[:n])
    return res


def _statement_fallback(stmts: Any) -> Optional[ast.AST]:
    """Used when executing cannot place the instruction:

    * Some STORE_ATTR/STORE_SUBSCR instructions are not placed: if the statement is one
      simple attribute/subscript assignment, use its target.
    * A suspended ``await`` (SEND/YIELD_VALUE) is not placed on any version: if the
      statement holds exactly one ``await <call>``, use that call.
    """
    if not stmts or len(stmts) != 1:
        return None
    (stmt,) = stmts
    awaits = [n for n in ast.walk(stmt) if isinstance(n, ast.Await)]
    if len(awaits) == 1 and isinstance(awaits[0].value, ast.Call):
        return awaits[0].value
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target = stmt.targets[0]
    elif isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
        target = stmt.target
    else:
        return None
    if isinstance(target, (ast.Attribute, ast.Subscript)):
        if not hasattr(target, "parent"):
            setattr(target, "parent", stmt)
        return target
    return None


def _classify(node: ast.AST, callee: Optional[CodeType]) -> Resolution:
    parent = getattr(node, "parent", None)

    if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, ast.Store):
        target = _unparse(node)
        if isinstance(parent, ast.Assign):
            return Resolution(True, "assign", value_node=parent.value, target_source=target)
        if isinstance(parent, ast.AnnAssign):
            return Resolution(True, "assign", value_node=parent.value, target_source=target)
        if isinstance(parent, ast.AugAssign):
            load = ast.parse(target, mode="eval").body
            value = ast.BinOp(left=load, op=parent.op, right=parent.value)
            return Resolution(True, "assign", value_node=value, target_source=target)
        if isinstance(parent, (ast.Tuple, ast.List)):
            grand = getattr(parent, "parent", None)
            if (
                isinstance(grand, ast.Assign)
                and isinstance(grand.value, (ast.Tuple, ast.List))
                and len(grand.value.elts) == len(parent.elts)
            ):
                elt = grand.value.elts[parent.elts.index(node)]
                return Resolution(True, "assign", value_node=elt, target_source=target)
        return Resolution(True, "assign", target_source=target, note="unsupported assignment form")

    if isinstance(node, ast.Call):
        args: list[ast.expr] = []
        for a in node.args:
            if isinstance(a, ast.Starred):
                break
            args.append(a)
        kwargs = {k.arg: k.value for k in node.keywords if k.arg is not None}
        params: tuple[str, ...] = ()
        if callee is not None:
            names = callee.co_varnames[: callee.co_argcount]
            is_method = isinstance(node.func, ast.Attribute) or callee.co_name == "__init__"
            if names and names[0] in ("self", "cls") and is_method:
                names = names[1:]
            params = tuple(names)
        return Resolution(
            True,
            "call",
            args=args,
            kwargs=kwargs,
            param_names=params,
            target_source=_unparse(node.func),
        )

    if isinstance(node, (ast.Attribute, ast.Subscript)):
        bound = None
        if (
            isinstance(parent, ast.Assign)
            and parent.value is node
            and len(parent.targets) == 1
            and isinstance(parent.targets[0], ast.Name)
        ):
            bound = parent.targets[0].id
        return Resolution(True, "read", target_source=_unparse(node), bound_to=bound)

    return Resolution(True, "other", target_source=_unparse(node))


# --------------------------------------------------------------------------- #
# Rendering of expressions (SYM-3, SYM-7)
# --------------------------------------------------------------------------- #


def _unparse(node: ast.AST) -> str:
    return ast.unparse(node)


def _literal_text(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return repr(value)


def render(node: ast.expr) -> Sym:
    """Literals stay literal, everything else becomes ``<expr>``; containers and
    f-strings are rendered part by part."""
    text, raw, literal = _render(node)
    return Sym(text=text, raw=raw, source=_unparse(node), literal=literal)


def _render(node: ast.expr) -> tuple[str, str, bool]:
    if isinstance(node, ast.Constant):
        text = _literal_text(node.value)
        raw = node.value if isinstance(node.value, str) else text
        return text, raw, True
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.USub, ast.UAdd))
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float, complex))
    ):
        text = _unparse(node)
        return text, text, True
    if isinstance(node, ast.Dict) and all(k is not None for k in node.keys):
        parts = []
        literal = True
        for k, v in zip(node.keys, node.values):
            kt, _, kl = _render(k)  # type: ignore[arg-type]
            if isinstance(k, ast.Constant) and isinstance(k.value, str) and is_secret_key(k.value):
                vt, vl = json.dumps(MASK), True  # ACT-9: literal secrets in source
            else:
                vt, _, vl = _render(v)
            literal = literal and kl and vl
            parts.append(f"{kt}: {vt}")
        text = "{" + ", ".join(parts) + "}"
        return text, text, literal
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        rendered = [_render(e) for e in node.elts]
        inner = ", ".join(r[0] for r in rendered)
        if isinstance(node, ast.List):
            text = f"[{inner}]"
        elif isinstance(node, ast.Set):
            text = "{" + inner + "}"
        else:
            text = f"({inner}{',' if len(rendered) == 1 else ''})"
        return text, text, all(r[2] for r in rendered)
    if isinstance(node, ast.JoinedStr):
        raw_parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                raw_parts.append(str(v.value))
            elif isinstance(v, ast.FormattedValue):
                raw_parts.append(f"<{_unparse(v.value)}>")
        raw = "".join(raw_parts)
        return f'"{raw}"', raw, False
    text = f"<{_unparse(node)}>"
    return text, text, False


def assert_source(tb: Any) -> Optional[str]:
    """Source of the ``assert`` that raised, from the innermost traceback entry
    (``"level < 15"``), or ``None`` when it was not a plain assert statement."""
    if tb is None:
        return None
    while tb.tb_next is not None:
        tb = tb.tb_next
    try:
        source = executing.Source.for_frame(tb.tb_frame)
        stmts = source.statements_at_line(tb.tb_lineno)
    except Exception:
        return None
    if len(stmts) == 1:
        (stmt,) = stmts
        if isinstance(stmt, ast.Assert):
            # As written, like pytest_assertion_pass reports passing asserts.
            return ast.get_source_segment(source.text, stmt.test) or _unparse(stmt.test)
    return None
