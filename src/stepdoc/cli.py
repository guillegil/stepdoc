"""The ``stepdoc`` command (OUT-6). Works without pytest installed.

    stepdoc render run.json --procedure procedure.html --report report.md
    stepdoc render run.json                      # procedure as Markdown on stdout
    stepdoc render run.json --doc report --format text
    stepdoc skill install                         # .claude/skills/ and .agents/skills/
    stepdoc skill status
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import skills
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

    sk = sub.add_parser("skill", help="install the stepdoc agent skill for AI coding agents")
    sk_sub = sk.add_subparsers(dest="skill_command", required=True)
    for name, text in (
        ("install", "write the skill where agents look for it"),
        ("uninstall", "remove stepdoc's skill folder"),
    ):
        p = sk_sub.add_parser(name, help=text, description=text[0].upper() + text[1:] + ".")
        only = p.add_mutually_exclusive_group()
        only.add_argument("--claude", action="store_true", help="only .claude/skills/ (Claude Code)")
        only.add_argument(
            "--agents", "--generic", dest="agents", action="store_true",
            help="only .agents/skills/ (Codex, GitHub Copilot and other agents)",
        )
        only.add_argument("--path", metavar="DIR", help="use DIR instead (the skill goes in DIR/stepdoc)")
        p.add_argument("--global", dest="global_", action="store_true",
                       help="use the home folder instead of the project")
        p.add_argument("--force", action="store_true", help="overwrite or remove a copy you edited")
        if name == "install":
            p.add_argument("--print", dest="print_", action="store_true",
                           help="write the skill to stdout instead of to disk")
    sk_sub.add_parser("status", help="list installed copies and whether they are outdated")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "skill":
        return _skill(args)
    return _render(args)


def _skill(args: argparse.Namespace) -> int:
    if args.skill_command == "status":
        found = list(skills.status())
        if not found:
            print("stepdoc skill: not installed")
        current = skills.stepdoc_version()
        for st in found:
            notes = []
            if st.outdated:
                notes.append(f"outdated: {st.version or 'unknown'} < {current}")
            if st.edited:
                notes.append("edited")
            print(f"{_shown(st.folder)}  {st.version or '?'}" + (f"  ({', '.join(notes)})" if notes else ""))
        return 0

    if getattr(args, "print_", False):
        sys.stdout.write(skills.bundle())
        return 0
    targets = ["claude"] if args.claude else ["agents"] if args.agents else list(skills.TARGETS)
    folders = skills.destinations(targets, global_=args.global_, path=args.path)
    action = skills.install if args.skill_command == "install" else skills.uninstall
    code = 0
    for folder in folders:
        result = action(folder, force=args.force)
        detail = f" ({result.detail})" if result.detail else ""
        print(f"stepdoc skill: {result.action} {_shown(result.folder)}{detail}")
        if result.action in ("skipped-edited", "not-stepdoc"):
            code = 1
    return code


def _shown(path: Path) -> str:
    """A path relative to the current folder when it is below it (SKL-2: print every path)."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def _render(args: argparse.Namespace) -> int:
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
