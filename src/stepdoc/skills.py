"""The agent skill shipped with stepdoc (SKL-1…9): install, status, uninstall.

The skill is package data in ``stepdoc/skill/`` (``SKILL.md`` plus ``references/``).
Installing copies it into each agent's skills folder as ``<folder>/stepdoc/``. A
small manifest next to the copy records what was written, so an edited copy is
never overwritten (or removed) without ``force``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Iterator, Optional, Sequence

NAME = "stepdoc"
MANIFEST = ".stepdoc-skill.json"
VERSION_PLACEHOLDER = "{{stepdoc_version}}"

# Where each agent looks for skills, relative to the project or the home folder.
# Keep this table the single place to update when conventions change.
TARGETS: dict[str, str] = {
    "claude": ".claude/skills",  # Claude Code
    "agents": ".agents/skills",  # Codex, GitHub Copilot in VS Code, and others
}


def stepdoc_version() -> str:
    try:
        from importlib.metadata import version

        return version("stepdoc")
    except Exception:
        return "unknown"


def skill_files(version: Optional[str] = None) -> dict[str, str]:
    """The skill as ``{relative path: text}``, with the stepdoc version filled in."""
    version = version or stepdoc_version()
    root = resources.files("stepdoc") / "skill"
    files: dict[str, str] = {}

    def walk(node: "resources.abc.Traversable", prefix: str) -> None:
        for child in sorted(node.iterdir(), key=lambda c: c.name):
            rel = f"{prefix}{child.name}"
            if child.is_dir():
                walk(child, rel + "/")
            elif child.name.endswith(".md"):
                files[rel] = child.read_text(encoding="utf-8").replace(VERSION_PLACEHOLDER, version)

    walk(root, "")
    return files


def project_root(start: Optional[Path] = None) -> Path:
    """The git repository root above ``start`` (default: the current folder), or
    ``start`` itself outside a repository (SKL-2)."""
    here = (start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / ".git").exists():
            return folder
    return here


def destinations(
    targets: Sequence[str] = tuple(TARGETS),
    *,
    global_: bool = False,
    path: Optional[str] = None,
    start: Optional[Path] = None,
) -> list[Path]:
    """Skill folders (``…/stepdoc``) for the chosen targets."""
    if path is not None:
        return [Path(path).expanduser() / NAME]
    base = Path.home() if global_ else project_root(start)
    return [base / TARGETS[t] / NAME for t in targets]


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_manifest(folder: Path) -> Optional[dict[str, str]]:
    try:
        data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        files = data.get("files")
        return files if isinstance(files, dict) else None
    except (OSError, ValueError):
        return None


def _current_files(folder: Path) -> dict[str, str]:
    out = {}
    for p in sorted(folder.rglob("*")):
        if p.is_file() and p.name != MANIFEST:
            out[p.relative_to(folder).as_posix()] = _digest(p.read_text(encoding="utf-8", errors="replace"))
    return out


def is_edited(folder: Path) -> bool:
    """True when the copy differs from what stepdoc wrote (or has no manifest)."""
    manifest = _read_manifest(folder)
    return manifest is None or manifest != _current_files(folder)


def installed_version(folder: Path) -> Optional[str]:
    try:
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^\s+stepdoc-version:\s*"?([^"\n]+)"?\s*$', text, re.MULTILINE)
    return m.group(1) if m else None


@dataclass
class Result:
    folder: Path
    action: str  # installed | updated | unchanged | skipped-edited | removed | absent | not-stepdoc
    detail: str = ""


def install(folder: Path, *, force: bool = False, files: Optional[dict[str, str]] = None) -> Result:
    """Write the skill into ``folder`` (SKL-4: an edited copy needs ``force``)."""
    files = files if files is not None else skill_files()
    wanted = {rel: _digest(text) for rel, text in files.items()}
    existed = folder.exists()
    if existed:
        if not force and is_edited(folder):
            return Result(folder, "skipped-edited", "edited since it was installed; use --force to overwrite")
        if _current_files(folder) == wanted:
            return Result(folder, "unchanged")
        shutil.rmtree(folder)
    for rel, text in files.items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    (folder / MANIFEST).write_text(json.dumps({"files": wanted}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return Result(folder, "updated" if existed else "installed")


def uninstall(folder: Path, *, force: bool = False) -> Result:
    """Remove stepdoc's own skill folder, and nothing else (SKL-5)."""
    if not folder.exists():
        return Result(folder, "absent")
    if folder.name != NAME or not (folder / "SKILL.md").exists():
        return Result(folder, "not-stepdoc", "does not look like the stepdoc skill; left alone")
    if not force and is_edited(folder):
        return Result(folder, "skipped-edited", "edited since it was installed; use --force to remove")
    shutil.rmtree(folder)
    return Result(folder, "removed")


@dataclass
class Status:
    folder: Path
    version: Optional[str]
    outdated: bool
    edited: bool


def status(start: Optional[Path] = None) -> Iterator[Status]:
    """Every installed copy, in the project and in the home folder (SKL-5)."""
    current = stepdoc_version()
    seen = set()
    for global_ in (False, True):
        for folder in destinations(global_=global_, start=start):
            key = os.path.realpath(folder)
            if key in seen or not folder.exists():
                continue
            seen.add(key)
            version = installed_version(folder)
            yield Status(folder, version, version != current, is_edited(folder))


def bundle(files: Optional[dict[str, str]] = None) -> str:
    """The skill as one text, for ``--print`` (SKL-6)."""
    files = files if files is not None else skill_files()
    parts = [files["SKILL.md"].rstrip("\n")]
    for rel, text in files.items():
        if rel != "SKILL.md":
            parts.append(f"\n<!-- ===== {rel} ===== -->\n\n{text.rstrip(chr(10))}")
    return "\n".join(parts) + "\n"
