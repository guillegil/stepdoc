"""Inline formatting in step titles: Markdown code spans (FMT-1).

A title is a plain string. Text between backticks is shown in monospace, as in
Markdown: ``step("Write into `dut.reg1 = 1`")``. :func:`mono` writes such a
span for any text, so it works inside f-strings::

    step(f"Write into {fmt.mono(reg.path)} = {value}")

Titles stay strings in the run record; renderers that can show monospace use
:func:`spans`, the others print the title as it is.
"""

from __future__ import annotations

import re

__all__ = ["mono", "spans"]

_TICKS = re.compile(r"`+")


def mono(text: object) -> str:
    """``text`` as a monospace span: wrapped in backticks, safe for any text.

    The fence is one backtick longer than the longest run inside ``text``, and
    padded with a space when ``text`` starts or ends with a backtick, as
    Markdown requires. Empty text gives ``""``.
    """
    s = str(text)
    if not s:
        return ""
    runs = [len(m.group()) for m in _TICKS.finditer(s)]
    fence = "`" * (max(runs) + 1 if runs else 1)
    pad = " " if s[0] == "`" or s[-1] == "`" else ""
    return f"{fence}{pad}{s}{pad}{fence}"


def spans(title: str) -> list[tuple[str, bool]]:
    """Split ``title`` into ``(text, is_code)`` runs.

    A run of backticks opens a code span that ends at the next run of the same
    length; an opening run with no match is plain text. One space is stripped
    from both ends of a span that has one on each side (Markdown's rule), so
    :func:`mono` round-trips. Adjacent plain runs are merged.
    """
    out: list[tuple[str, bool]] = []

    def add(text: str, code: bool) -> None:
        if not text:
            return
        if out and not code and not out[-1][1]:
            out[-1] = (out[-1][0] + text, False)
        else:
            out.append((text, code))

    pos = 0
    while True:
        m = _TICKS.search(title, pos)
        if m is None:
            add(title[pos:], False)
            return out
        n = len(m.group())
        close = None
        for c in _TICKS.finditer(title, m.end()):
            if len(c.group()) == n:
                close = c
                break
        if close is None:
            add(title[pos : m.end()], False)
            pos = m.end()
            continue
        add(title[pos : m.start()], False)
        body = title[m.end() : close.start()]
        if len(body) >= 2 and body[0] == " " and body[-1] == " " and body.strip():
            body = body[1:-1]
        if body.strip():
            add(body, True)
        else:  # nothing to show in monospace: keep the backticks as text
            add(title[m.start() : close.end()], False)
        pos = close.end()
