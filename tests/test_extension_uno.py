"""Run the extension's UNO smoke checks against a real LibreOffice.

The checks live in ``uno_smoke.py`` and run under the *system* Python, because
``uno`` is only importable from the interpreter LibreOffice was built against —
never from this project's virtualenv. That is the same constraint the extension
itself lives under, so shelling out is not a workaround here, it is the honest
way to test the thing.

Skipped when there is no LibreOffice, or no pyuno for it, exactly like
``test_libreoffice_roundtrip.py``. CI installs neither.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SMOKE = ROOT / "tests" / "uno_smoke.py"

SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")
UNOPKG = shutil.which("unopkg")


def _system_python() -> str | None:
    """An interpreter that can ``import uno``, or None."""
    for candidate in ("/usr/bin/python3", "python3"):
        executable = shutil.which(candidate) if not candidate.startswith("/") else candidate
        if not executable or not os.path.isfile(executable):
            continue
        probe = subprocess.run(
            [executable, "-c", "import uno"],
            capture_output=True,
            env={"PATH": "/usr/bin:/bin"},
        )
        if probe.returncode == 0:
            return executable
    return None


PYTHON = _system_python()

pytestmark = pytest.mark.skipif(
    not (SOFFICE and UNOPKG and PYTHON),
    reason="needs LibreOffice, unopkg and a python that can import uno",
)


def test_the_extension_installs_and_drives_a_real_document():
    """Installs the .oxt, then exercises linting, highlighting, fixing, undo and
    navigation against a document open in Calc."""
    environment = dict(os.environ)
    environment.pop("VIRTUAL_ENV", None)
    # LibreOffice's script provider resolves python3 from PATH; leaving the
    # project venv there makes it fail to open any document with an annotation.
    environment["PATH"] = "/usr/bin:/bin"

    result = subprocess.run(
        [PYTHON, str(SMOKE)],
        capture_output=True,
        text=True,
        timeout=600,
        env=environment,
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        failures = [line for line in result.stdout.splitlines() if line.startswith("FAIL:")]
        pytest.fail(
            "extension smoke checks failed:\n"
            + ("\n".join(failures) if failures else result.stdout)
            + "\n"
            + result.stderr[-2000:]
        )

    assert "0 failed" in result.stdout


def test_the_smoke_script_is_not_importable_by_the_project_venv():
    """A guard on the arrangement above: if someone 'helpfully' makes this
    importable, it will be collected by pytest and fail on `import uno`."""
    assert SMOKE.name not in [p.name for p in ROOT.glob("tests/test_*.py")]
    assert sys.modules.get("uno") is None
