"""Redaction of secrets and personal data (ACT-9).

Applied in ``record_action`` before anything is stored, so nothing downstream
(the run record, renderers, other tools) ever sees the original value.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Pattern

MASK = "***"

DEFAULT_KEYS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "x-api-key",
        "client_secret",
    }
)
DEFAULT_PATTERNS: tuple[str, ...] = (r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+",)

_keys: frozenset[str] = DEFAULT_KEYS
_patterns: tuple[Pattern[str], ...] = tuple(re.compile(p) for p in DEFAULT_PATTERNS)


def configure_redaction(
    keys: Optional[Iterable[str]] = None,
    patterns: Optional[Iterable[str]] = None,
    *,
    extend: bool = True,
) -> None:
    """Set the mapping keys (case-insensitive) whose values are masked, and regular
    expressions whose matches are masked inside strings. With ``extend=False`` the
    defaults are replaced instead of extended."""
    global _keys, _patterns
    base_keys = DEFAULT_KEYS if extend else frozenset()
    base_patterns = DEFAULT_PATTERNS if extend else ()
    _keys = base_keys | {k.lower() for k in (keys or ())}
    _patterns = tuple(re.compile(p) for p in (*base_patterns, *(patterns or ())))
    _text_cache.clear()


def is_secret_key(key: str) -> bool:
    return key.lower() in _keys


_text_cache: dict[str, str] = {}
_SCALARS = (int, float, bool, type(None))


def redact_text(text: str) -> str:
    cached = _text_cache.get(text)
    if cached is not None:
        return cached
    out = text
    for pattern in _patterns:
        out = pattern.sub(_mask_match, out)
    if len(_text_cache) > 4096:
        _text_cache.clear()
    _text_cache[text] = out
    return out


def _mask_match(m: "re.Match[str]") -> str:
    # Keep the scheme word ("Bearer ***") when the pattern has a first group.
    if m.lastindex:
        return f"{m.group(1)} {MASK}"
    return MASK


def redact(value: Any) -> Any:
    """Return a copy of ``value`` with secrets masked. Containers are copied only
    when something inside them changes, so the common case allocates nothing."""
    if isinstance(value, _SCALARS):
        return value
    if isinstance(value, str):
        return redact_text(value) if _patterns else value
    if isinstance(value, dict):
        out = None
        for k, v in value.items():
            new = MASK if isinstance(k, str) and is_secret_key(k) else redact(v)
            if new is not v and out is None:
                out = dict(value)
            if out is not None:
                out[k] = new
        return value if out is None else out
    if isinstance(value, (list, tuple)):
        items = [redact(v) for v in value]
        if all(a is b for a, b in zip(items, value)):
            return value
        if hasattr(value, "_fields"):  # namedtuple
            return type(value)(*items)
        return tuple(items) if isinstance(value, tuple) else items
    return value
