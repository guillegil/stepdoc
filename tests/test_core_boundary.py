"""NFR-9: stepdoc.core imports and works with pytest blocked."""

import subprocess
import sys
import textwrap
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def test_core_works_without_pytest(tmp_path):
    script = textwrap.dedent(
        """
        import sys
        sys.modules["pytest"] = None  # any `import pytest` now raises ImportError
        sys.modules["_pytest"] = None
        import stepdoc
        from stepdoc.core import Recorder, record_action, step

        def driver_write(value):
            record_action("dev.reg", "write", value)

        with Recorder() as rec:
            width = 5
            with step("Set width"):
                driver_write(width)
        rec.resolve()
        assert rec.steps[0].actions[0].symbolic_value == "<width>", rec.steps[0].actions[0]
        assert "pytest" not in {m.split(".")[0] for m in sys.modules if sys.modules[m] is not None}
        print("ok")
        """
    )
    path = tmp_path / "station_script.py"  # a real file, so the source can be found
    path.write_text(script)
    out = subprocess.run(
        [sys.executable, str(path)],
        env={"PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
