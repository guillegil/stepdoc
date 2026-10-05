"""The ``stepdoc`` command (OUT-6). Works without pytest installed.

    stepdoc render run.json --procedure procedure.html --report report.md
    stepdoc render run.json                      # procedure as Markdown on stdout
    stepdoc render run.json --doc report --format text
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .core.record import load
from .renderers import FORMATS, Doc, format_for_path, render


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stepdoc", description="Turn test code into test documentation.")
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser(
        "render",
        help="render a JSON run record into a procedure or report",
        description="Render a JSON run record (written with --stepdoc-record) without running the tests.",
    )
    r.add_argument("record", help="path of the JSON run record")
    r.add_argument("--procedure", metavar="PATH", help="write the procedure here")
    r.add_argument("--report", metavar="PATH", help="write the executed report here")
    r.add_argument(
        "--format",
        choices=sorted(FORMATS),
        help="output format; by default taken from each file's extension (.md, .html, .txt), "
        "Markdown on stdout",
    )
    r.add_argument(
        "--doc",
        choices=["procedure", "report"],
        default="procedure",
        help="document printed to stdout when neither --procedure nor --report is given",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        record = load(args.record)
    except (OSError, ValueError) as exc:  # json.JSONDecodeError is a ValueError
        kind = "invalid JSON" if isinstance(exc, json.JSONDecodeError) else str(exc)
        print(f"stepdoc: cannot read {args.record}: {kind}", file=sys.stderr)
        return 1

    wanted: list[tuple[Doc, Optional[str]]] = [("procedure", args.procedure), ("report", args.report)]
    targets = [(doc, path) for doc, path in wanted if path]
    if not targets:
        sys.stdout.write(render(record, args.doc, args.format or "md"))
        return 0
    for doc, path in targets:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(record, doc, args.format or format_for_path(path)), encoding="utf-8")
        print(f"stepdoc: wrote {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
