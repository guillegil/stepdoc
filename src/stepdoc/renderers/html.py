"""Self-contained HTML procedure and report (OUT-3): one file, inline CSS, no
scripts, readable on screen and in print. Rendered from the JSON run record."""

from __future__ import annotations

from html import escape
from typing import Optional

from .common import (
    OUTCOME_TEXT,
    STATUS_MARK,
    Line,
    Procedure,
    Record,
    anchors,
    procedures,
    short_name,
    show,
    test_lines,
)

_CSS = """
:root {
  --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --line: #d1d9e0; --code-bg: #f6f8fa;
  --pass: #1a7f37; --fail: #cf222e; --unknown: #9a6700; --step-bg: #f6f8fa;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --line: #3d444d; --code-bg: #161b22;
    --pass: #3fb950; --fail: #f85149; --unknown: #d29922; --step-bg: #161b22;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 960px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 28px; margin: 0 0 4px; }
h2 { font-size: 20px; margin: 40px 0 4px; padding-top: 8px; border-top: 1px solid var(--line); }
h3 { font-size: 15px; margin: 20px 0 8px; }
a { color: inherit; }
.meta { color: var(--muted); font-size: 13px; margin: 0 0 12px; overflow-wrap: anywhere; }
code, .code {
  font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: var(--code-bg); border-radius: 4px; padding: 1px 5px; overflow-wrap: anywhere;
}
table { border-collapse: collapse; margin: 8px 0 16px; width: 100%; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: 13px; color: var(--muted); font-weight: 600; }
nav ol { padding-left: 20px; margin: 8px 0; }
.lines { margin: 8px 0 16px; }
.line { display: flex; gap: 8px; align-items: baseline; padding: 3px 0; }
.line.step { font-weight: 600; margin-top: 6px; padding: 4px 8px; background: var(--step-bg); border-radius: 6px; }
.line.section { font-weight: 700; text-transform: uppercase; font-size: 12px; letter-spacing: .05em; color: var(--muted); margin-top: 12px; }
.num { color: var(--muted); min-width: 3.5em; font-variant-numeric: tabular-nums; }
.body { flex: 1; min-width: 0; }
.result { color: var(--muted); }
.note { color: var(--muted); font-size: 13px; font-weight: 400; }
.mark { font-weight: 700; min-width: 1em; }
.passed, .mark-pass { color: var(--pass); }
.failed, .error, .mark-fail { color: var(--fail); }
.unknown, .skipped, .mark-unknown { color: var(--unknown); }
.badge { font-size: 12px; font-weight: 700; padding: 1px 8px; border-radius: 10px; border: 1px solid currentColor; }
@media print {
  main { max-width: none; padding: 0; }
  h2 { break-before: page; }
  .line { break-inside: avoid; }
}
"""

_MARK_CLASS = {"✓": "mark-pass", "✗": "mark-fail", "?": "mark-unknown", "·": "mark-unknown"}


def _page(title: str, record: Record, body: list[str]) -> str:
    gen = record.get("generator") or {}
    meta = f"stepdoc {gen.get('version', '')} · {record.get('created_at', '')}"
    if record.get("dry_run"):
        meta += " · dry run: results were not judged"
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{escape(title)}</title>",
            f"<style>{_CSS}</style>",
            "</head>",
            "<body><main>",
            f"<h1>{escape(title)}</h1>",
            f'<p class="meta">{escape(meta)}</p>',
            *body,
            "</main></body>",
            "</html>",
            "",
        ]
    )


def _code(text: str) -> str:
    return f"<code>{escape(text)}</code>" if text else ""


def _line(line: Line) -> str:
    pad = f' style="margin-left:{line.depth * 1.5}em"' if line.depth else ""
    if line.is_step and line.number is None:
        return f'<div class="line section"{pad}>{escape(line.code)}</div>'
    cls = "line step" if line.is_step else "line"
    num = f'<span class="num">{escape(line.number)}</span>' if line.number else '<span class="num">·</span>'
    if line.is_step:
        body = escape(line.code)
    else:
        body = " ".join(p for p in (escape(line.verb), _code(line.code)) if p)
        if line.result:
            body += f' <span class="result">→ {_code(line.result)}</span>'
    if line.note:
        body += f' <span class="note">{escape(line.note)}</span>'
    mark = ""
    if line.mark:
        mark = f'<span class="mark {_MARK_CLASS.get(line.mark, "")}">{escape(line.mark)}</span>'
    return f'<div class="{cls}"{pad}>{num}<span class="body">{body}</span>{mark}</div>'


def _lines(lines: list[Line]) -> str:
    return '<div class="lines">' + "".join(_line(line) for line in lines) + "</div>"


def _toc(items: list[tuple[str, str, Optional[str]]]) -> list[str]:
    """``(anchor, label, status class)`` per entry. Only for more than one entry."""
    if len(items) < 2:
        return []
    out = ["<nav><h3>Contents</h3><ol>"]
    for anchor, label, cls in items:
        badge = f' <span class="badge {cls}">{escape(cls.upper())}</span>' if cls else ""
        out.append(f'<li><a href="#{anchor}">{escape(label)}</a>{badge}</li>')
    out.append("</ol></nav>")
    return out


def _params_table(proc: Procedure) -> list[str]:
    params = proc.params
    if not params:
        return []
    rows = "".join(
        f"<tr><td>{_code(name)}</td><td>{', '.join(_code(v) for v in values)}</td></tr>"
        for name, values in params.items()
    )
    return ["<h3>Parameters</h3>", f"<table><tr><th>Parameter</th><th>Values</th></tr>{rows}</table>"]


def render_procedure(record: Record, title: str = "Test procedures") -> str:
    procs = procedures(record)
    ids = anchors([p.procedure_id for p in procs])
    body = _toc([(a, short_name(p.procedure_id), None) for a, p in zip(ids, procs)])
    for anchor, proc in zip(ids, procs):
        n = len(proc.cases)
        body.append(f'<h2 id="{anchor}">{escape(short_name(proc.procedure_id))}</h2>')
        body.append(f'<p class="meta">{escape(proc.procedure_id)} · {n} case{"s" * (n != 1)}</p>')
        body += _params_table(proc)
        if proc.setup is not None:
            body.append(_lines(test_lines(proc.setup, symbolic=True, sections=("setup",))))
        only = ("procedure",)
        for _, members in proc.variants:
            if len(proc.variants) > 1:
                cases = ", ".join(_code(m["case"] or m["id"]) for m in members)
                body.append(f"<h3>Procedure for {cases}</h3>")
            longest = max(members, key=lambda m: len(test_lines(m, symbolic=True, sections=only)))
            body.append(_lines(test_lines(longest, symbolic=True, sections=only)))
        if proc.teardown is not None:
            body.append(_lines(test_lines(proc.teardown, symbolic=True, sections=("teardown",))))
    return _page(title, record, body)


def _outcome(test: Record) -> tuple[str, str]:
    if test["dry_run"]:
        return "DRY RUN", "unknown"
    text = OUTCOME_TEXT.get(test["outcome"], "?")
    return text, test["outcome"] or "unknown"


def render_report(record: Record, title: str = "Test report") -> str:
    tests = record["tests"]
    ids = anchors([t["id"] for t in tests])
    rows = []
    for anchor, t in zip(ids, tests):
        text, cls = _outcome(t)
        mark = STATUS_MARK.get(t["status"], "?")
        rows.append(
            f'<tr><td><a href="#{anchor}"><code>{escape(t["id"])}</code></a></td>'
            f'<td><span class="badge {cls}">{escape(text)}</span></td>'
            f'<td class="{_MARK_CLASS.get(mark, "")}">{escape(mark)}</td></tr>'
        )
    body = [
        "<table><tr><th>Test</th><th>Outcome</th><th>Steps</th></tr>" + "".join(rows) + "</table>"
    ]
    for anchor, t in zip(ids, tests):
        text, cls = _outcome(t)
        body.append(
            f'<h2 id="{anchor}">{escape(short_name(t["id"]))} <span class="badge {cls}">{escape(text)}</span></h2>'
        )
        meta = [t["id"], t["started_at"]]
        if t["duration"] is not None:
            meta.append(f"{t['duration'] * 1000:.0f} ms")
        if t["seed"] is not None:
            meta.append(f"seed {show(t['seed'])}")
        body.append(f'<p class="meta">{escape(" · ".join(meta))}</p>')
        if t["params"]:
            params = ", ".join(_code(f"{k} = {show(v)}") for k, v in t["params"].items())
            body.append(f"<p>Parameters: {params}</p>")
        body.append(_lines(test_lines(t, symbolic=False)))
    return _page(title, record, body)
