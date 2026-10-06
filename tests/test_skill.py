"""SKL-1…10: the agent skill and `stepdoc skill`."""

import re
import shlex
from pathlib import Path

import pytest

import stepdoc
from stepdoc import skills
from stepdoc.cli import _parser, main

SKILL_DIR = Path(stepdoc.__file__).parent / "skill"
FILES = skills.skill_files("9.9.9")
ALL_TEXT = "\n".join(FILES.values())


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, "SKILL.md must start with YAML frontmatter"
    data, section = {}, None
    for line in m.group(1).splitlines():
        if line.startswith("  ") and section:
            k, _, v = line.strip().partition(":")
            data[section][k] = v.strip().strip('"')
        else:
            k, _, v = line.partition(":")
            section = k if not v.strip() else None
            data[k] = {} if section else v.strip()
    return data


def code_blocks(text, lang=None):
    for m in re.finditer(r"```(\w*)\n(.*?)```", text, re.DOTALL):
        if lang is None or m.group(1) == lang:
            yield m.group(2)


# --------------------------------------------------------------------------- #
# SKL-7, SKL-10: format
# --------------------------------------------------------------------------- #


def test_frontmatter_follows_the_agent_skills_spec():
    fm = frontmatter(FILES["SKILL.md"])
    assert fm["name"] == "stepdoc" == skills.NAME  # must match the folder name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fm["name"]) and len(fm["name"]) <= 64
    assert 0 < len(fm["description"]) <= 1024 and "Use when" in fm["description"]
    assert fm["metadata"] == {"stepdoc-version": "9.9.9"}
    assert set(fm) <= {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


def test_main_file_is_short_and_links_resolve():
    text = FILES["SKILL.md"]
    assert len(text.splitlines()) < 500
    for name, body in FILES.items():
        base = Path(name).parent
        for link in re.findall(r"\]\(([^)#:]+\.md)\)", body):
            assert (base / link).as_posix() in FILES, f"{name} links to missing {link}"
    assert {"references/bridges.md", "references/checks.md", "references/review.md"} <= set(FILES)


# --------------------------------------------------------------------------- #
# SKL-10: every name the skill mentions exists
# --------------------------------------------------------------------------- #


def test_api_names_exist():
    names = set(re.findall(r"\bstepdoc\.([A-Za-z_]\w*)", ALL_TEXT))
    for m in re.finditer(r"from stepdoc import ([\w, ]+)", ALL_TEXT):
        names |= {n.strip() for n in m.group(1).split(",")}
    names -= {"core", "md", "html", "txt"}  # module and file names in prose
    assert names, "expected API names in the skill"
    missing = sorted(n for n in names if not hasattr(stepdoc, n))
    assert not missing, f"the skill mentions APIs that do not exist: {missing}"


def test_options_and_ini_names_exist():
    plugin = (Path(stepdoc.__file__).parent / "pytest_plugin.py").read_text()
    for opt in set(re.findall(r"--stepdoc-[a-z-]+", ALL_TEXT)):
        assert f'"{opt}"' in plugin, opt
    for ini in set(re.findall(r"\bstepdoc_[a-z_]+\b", ALL_TEXT)):
        assert f'"{ini}"' in plugin or f"def {ini}(" in plugin, ini


def test_documented_commands_parse():
    commands = []
    for block in code_blocks(ALL_TEXT, "bash"):
        for line in block.splitlines():
            line = line.split("#", 1)[0].strip()
            if line.startswith("stepdoc "):
                commands.append(shlex.split(line)[1:])
    assert any(c[0] == "render" for c in commands)
    for argv in commands:
        _parser().parse_args(argv)  # SystemExit on an unknown command or flag


# --------------------------------------------------------------------------- #
# SKL-10: the worked example runs and documents what it says
# --------------------------------------------------------------------------- #


def test_example_runs(pytester):
    files = {}
    for block in code_blocks(FILES["references/example.md"]):
        first, _, rest = block.partition("\n")
        if first.startswith("# ") and "." in first:
            files[first[2:].strip()] = rest
    assert set(files) == {"mydevice.py", "conftest.py", "test_levels.py", "pytest.ini"}
    for name, body in files.items():
        (pytester.path / name).write_text(body)
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "--stepdoc-procedure=procedure.txt")
    result.assert_outcomes(passed=2)
    procedure = (pytester.path / "procedure.txt").read_text()
    expected = list(code_blocks(FILES["references/example.md"], "text"))[-1]
    for line in expected.strip().splitlines():
        assert line in procedure, f"{line!r} not in\n{procedure}"


# --------------------------------------------------------------------------- #
# SKL-2…6: install, status, uninstall
# --------------------------------------------------------------------------- #


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "pkg").mkdir()
    monkeypatch.chdir(root / "pkg")  # below the root: installs go to the root
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(skills, "stepdoc_version", lambda: "1.0")
    return root


def test_install_both_targets_then_unchanged(project, capsys):
    assert main(["skill", "install"]) == 0
    out = capsys.readouterr().out
    for target in (".claude/skills/stepdoc", ".agents/skills/stepdoc"):
        assert (project / target / "SKILL.md").exists()
        assert (project / target / "references/bridges.md").exists()
        assert f"installed {project / target}" in out
    assert 'stepdoc-version: "1.0"' in (project / ".claude/skills/stepdoc/SKILL.md").read_text()
    assert main(["skill", "install"]) == 0
    assert capsys.readouterr().out.count("unchanged") == 2


def test_target_flags(project, tmp_path):
    main(["skill", "install", "--claude"])
    assert (project / ".claude/skills/stepdoc").exists() and not (project / ".agents").exists()
    main(["skill", "install", "--generic", "--global"])
    assert (tmp_path / "home/.agents/skills/stepdoc/SKILL.md").exists()
    main(["skill", "install", "--path", str(tmp_path / "custom")])
    assert (tmp_path / "custom/stepdoc/SKILL.md").exists()


def test_edited_copy_is_never_overwritten_without_force(project, capsys):
    main(["skill", "install", "--claude"])
    skill = project / ".claude/skills/stepdoc/SKILL.md"
    skill.write_text(skill.read_text() + "\nMy team's notes.\n")
    assert main(["skill", "install", "--claude"]) == 1
    assert "use --force" in capsys.readouterr().out
    assert "My team's notes." in skill.read_text()
    assert main(["skill", "uninstall", "--claude"]) == 1
    assert skill.exists()
    assert main(["skill", "install", "--claude", "--force"]) == 0
    assert "My team's notes." not in skill.read_text()


def test_update_when_stepdoc_changes(project, monkeypatch, capsys):
    main(["skill", "install", "--claude"])
    capsys.readouterr()
    main(["skill", "status"])
    assert "(outdated" not in capsys.readouterr().out
    monkeypatch.setattr(skills, "stepdoc_version", lambda: "2.0")
    main(["skill", "status"])
    assert "outdated: 1.0 < 2.0" in capsys.readouterr().out
    main(["skill", "install", "--claude"])
    assert "updated" in capsys.readouterr().out
    assert 'stepdoc-version: "2.0"' in (project / ".claude/skills/stepdoc/SKILL.md").read_text()


def test_uninstall_removes_only_its_folder(project, capsys):
    main(["skill", "install"])
    other = project / ".claude/skills/other-skill"
    other.mkdir()
    (other / "SKILL.md").write_text("---\nname: other-skill\n---\n")
    assert main(["skill", "uninstall"]) == 0
    assert not (project / ".claude/skills/stepdoc").exists()
    assert not (project / ".agents/skills/stepdoc").exists()
    assert other.exists()
    main(["skill", "status"])
    assert "not installed" in capsys.readouterr().out


def test_print_writes_nothing(project, capsys):
    assert main(["skill", "install", "--print"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("---\nname: stepdoc") and "===== references/bridges.md =====" in out
    assert not (project / ".claude").exists()


def test_outside_a_repository_uses_the_current_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert skills.project_root() == tmp_path.resolve()
