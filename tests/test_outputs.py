"""OUT-3, OUT-5, OUT-6: HTML, contents and test ids, and `stepdoc render`."""

import json
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

from stepdoc import Recorder, step
from stepdoc.cli import main
from stepdoc.core.record import case_record, dumps, run_record
from stepdoc.renderers import format_for_path, render
from stepdoc.renderers.common import anchors

SRC = Path(__file__).resolve().parents[1] / "src"


def make_run(dev, api):
    cases = []
    for level in (1, 2):
        with Recorder() as rec:
            with step("Set level"):
                dev.map.dac.level = level
        cases.append(case_record(rec, test_id=f"t.py::test_dev[{level}]", procedure_id="t.py::test_dev",
                                 case=str(level), params={"level": level}, outcome="passed"))
    with Recorder() as rec:
        with step("Create user"):
            name = "<script>"
            api.post("/users", json={"name": name, "age": 3})
    cases.append(case_record(rec, test_id="t.py::test_api", outcome="failed"))
    return run_record(cases)


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.ids, self.hrefs, self.srcs = [], set(), [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append(tag)
        if "id" in a:
            self.ids.add(a["id"])
        if "href" in a:
            self.hrefs.append(a["href"])
        if "src" in a:
            self.srcs.append(a["src"])


def test_html_is_self_contained_and_escaped(dev, api):
    record = make_run(dev, api)
    for doc in ("procedure", "report"):
        page = render(record, doc, "html")
        tags = Tags()
        tags.feed(page)
        assert page.startswith("<!doctype html>")
        assert "script" not in tags.tags and "link" not in tags.tags and not tags.srcs
        assert "<script>" not in page  # the test value is escaped
        # Every contents link points at a section on the page.
        local = [h[1:] for h in tags.hrefs if h.startswith("#")]
        assert local and set(local) <= tags.ids
    procedure = render(record, "procedure", "html")
    assert "&lt;level&gt;" in procedure and "&lt;name&gt;" in procedure
    report = render(record, "report", "html")
    assert '"&lt;script&gt;"' in report or "&#x27;&lt;script&gt;&#x27;" in report
    assert 'class="badge failed"' in report


def test_markdown_contents_link_to_anchors(dev, api):
    record = make_run(dev, api)
    procedure = render(record, "procedure", "md")
    assert "**Contents**" in procedure
    assert "1. [test_dev](#t-py-test_dev) · `t.py::test_dev`" in procedure
    assert '<a id="t-py-test_dev"></a>' in procedure
    report = render(record, "report", "md")
    assert "| [`t.py::test_dev[1]`](#t-py-test_dev-1) | PASSED | ✓ |" in report
    assert '<a id="t-py-test_dev-1"></a>' in report


def test_anchors_are_unique():
    assert anchors(["a::b", "a.b", "A::B", ""]) == ["a-b", "a-b-2", "a-b-3", "test"]


def test_format_from_extension():
    assert [format_for_path(p) for p in ("r.html", "R.HTM", "p.md", "x.txt", "noext")] == [
        "html", "html", "md", "text", "md",
    ]
    with pytest.raises(ValueError, match="unknown format"):
        render({"tests": []}, "procedure", "docx")


def test_cli_renders_files_and_stdout(dev, api, tmp_path, capsys):
    path = tmp_path / "run.json"
    path.write_text(dumps(make_run(dev, api)))
    assert main(["render", str(path), "--procedure", str(tmp_path / "out/p.html"),
                 "--report", str(tmp_path / "out/r.md")]) == 0
    assert (tmp_path / "out/p.html").read_text().startswith("<!doctype html>")
    assert (tmp_path / "out/r.md").read_text().startswith("# Test report")
    assert "stepdoc: wrote" in capsys.readouterr().out

    assert main(["render", str(path), "--doc", "report", "--format", "text"]) == 0
    assert "t.py::test_api   FAILED" in capsys.readouterr().out


def test_cli_rejects_bad_records(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    assert main(["render", str(bad)]) == 1
    assert "invalid JSON" in capsys.readouterr().err
    bad.write_text(json.dumps({"schema_version": "9"}))
    assert main(["render", str(bad)]) == 1
    assert "unsupported run record schema" in capsys.readouterr().err


def test_cli_runs_without_pytest(dev, api, tmp_path):
    path = tmp_path / "run.json"
    path.write_text(dumps(make_run(dev, api)))
    script = (
        "import sys; sys.modules['pytest'] = None; sys.modules['_pytest'] = None\n"
        "from stepdoc.cli import main\n"
        f"sys.exit(main(['render', {str(path)!r}, '--format', 'html']))\n"
    )
    out = subprocess.run([sys.executable, "-c", script], env={"PYTHONPATH": str(SRC)},
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("<!doctype html>")
