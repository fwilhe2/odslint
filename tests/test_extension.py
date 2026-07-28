"""Tests for the extension's non-UNO half.

Everything in ``odslint_core`` runs under plain pytest. The UNO half needs a
live LibreOffice and is covered by ``test_extension_uno.py``, which skips itself
when ``uno`` is not importable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from helpers import fixture

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "extension" / "python" / "pythonpath"))

import odslint_core as core  # noqa: E402

# -- reading the report -----------------------------------------------------


def _report(path, unsafe=False):
    """The real CLI output, so the extension is tested against the real contract."""
    command = [sys.executable, "-m", "odslint", "--format", "json", "--fail-on", "never"]
    if unsafe:
        command.append("--unsafe-fixes")
    command.append(str(path))
    result = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_findings_are_parsed_from_the_real_cli_output():
    findings = core.parse_report(_report(fixture("named_ranges.fods")))
    assert findings
    assert all(f.sheet == "Model" for f in findings)
    assert any(f.is_fixable for f in findings)


def test_an_empty_report_is_no_findings():
    assert core.parse_report("[]") == []
    assert core.parse_report("   ") == []


def test_findings_sort_errors_first():
    findings = core.parse_report(
        json.dumps(
            [
                {"rule": "b", "severity": "warning", "sheet": "S", "row": 0, "column": 0},
                {"rule": "a", "severity": "error", "sheet": "S", "row": 5, "column": 0},
            ]
        )
    )
    findings.sort(key=lambda f: f.sort_key())
    assert [f.severity for f in findings] == ["error", "warning"]


def test_a_sheet_level_finding_is_not_navigable():
    finding = core.Finding({"rule": "x", "sheet": "S", "message": "m"})
    assert not finding.is_cell_anchored
    assert finding.location == "S"


def test_a_fixable_finding_is_marked_in_its_label():
    findings = core.parse_report(_report(fixture("named_ranges.fods")))
    fixable = [f for f in findings if f.is_fixable]
    assert fixable
    assert "[*]" in fixable[0].label()


# -- choosing edits ---------------------------------------------------------


def test_only_safe_edits_are_collected_by_default():
    findings = core.parse_report(_report(fixture("text_numbers.fods"), unsafe=True))
    assert any(f.is_fixable for f in findings)
    assert core.collect_edits(findings) == []
    assert core.collect_edits(findings, unsafe=True)


def test_one_cell_is_only_claimed_once():
    """Mirrors odslint.fixer.plan_fixes: a second rule wanting the same cell
    would otherwise silently overwrite the first."""
    edit = {"sheet": "S", "row": 0, "column": 0, "kind": "formula", "formula_a1": "=1"}
    findings = [
        core.Finding({"rule": "a", "fix": {"applicability": "safe", "edits": [edit]}}),
        core.Finding({"rule": "b", "fix": {"applicability": "safe", "edits": [edit]}}),
    ]
    assert len(core.collect_edits(findings)) == 1


def test_every_formula_edit_carries_the_a1_spelling():
    """The extension applies fixes via setFormula, which needs A1. Without this
    the fix would be silently stored as a broken formula."""
    for name in ("named_ranges.fods", "inconsistent_formulas.fods"):
        findings = core.parse_report(_report(fixture(name), unsafe=True))
        for edit in core.collect_edits(findings, unsafe=True):
            if edit["kind"] == "formula":
                assert edit["formula_a1"], f"{name}: {edit} has no A1 spelling"
                assert "[" not in edit["formula_a1"]


# -- finding the executable -------------------------------------------------


def test_a_configured_path_is_used_when_executable(tmp_path):
    exe = tmp_path / "odslint"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    assert core.find_interpreter(str(exe)) == [str(exe)]


def test_a_configured_path_that_is_not_executable_is_an_error(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(core.OdslintNotFound, match="not executable"):
        core.find_interpreter(str(missing))


def test_path_is_searched_when_nothing_is_configured(tmp_path):
    exe = tmp_path / "odslint"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    assert core.find_interpreter(None, env={"PATH": str(tmp_path)}) == [str(exe)]


def test_a_missing_executable_explains_how_to_install_it(monkeypatch):
    monkeypatch.setattr(core.os.path, "isfile", lambda _: False)
    with pytest.raises(core.OdslintNotFound, match="pip install"):
        core.find_interpreter(None, env={"PATH": ""})


def test_the_command_never_asks_the_cli_to_write():
    """The extension applies fixes itself through UNO, so that they land in
    Calc's undo stack; the CLI is only ever asked to report."""
    command = core.build_command(["odslint"], "/tmp/x.fods", unsafe=True)
    assert "--fix" not in command
    assert "--format" in command and "json" in command
    assert command[-1] == "/tmp/x.fods"


def test_a_tool_error_is_raised_rather_than_read_as_a_clean_file(tmp_path):
    """Exit code 2 with no output must not look like "no problems found"."""
    broken = tmp_path / "broken.fods"
    broken.write_text("not xml at all")
    with pytest.raises(core.OdslintFailed):
        core.run_odslint([sys.executable, "-m", "odslint"], str(broken))


def test_running_the_real_cli_returns_findings():
    findings = core.run_odslint(
        [sys.executable, "-m", "odslint"], str(fixture("magic_numbers.fods"))
    )
    assert findings
    assert findings[0].rule


# -- settings ---------------------------------------------------------------


def test_settings_round_trip(tmp_path):
    path = tmp_path / "sub" / "odslint.json"
    core.save_settings(str(path), {"interpreter": "/x/odslint", "highlight": False})
    loaded = core.load_settings(str(path))
    assert loaded["interpreter"] == "/x/odslint"
    assert loaded["highlight"] is False
    # Unspecified keys keep their defaults.
    assert loaded["unsafe_fixes"] is False


def test_missing_or_corrupt_settings_fall_back_to_defaults(tmp_path):
    assert core.load_settings(str(tmp_path / "nothing.json")) == core.DEFAULT_SETTINGS
    corrupt = tmp_path / "bad.json"
    corrupt.write_text("{not json")
    assert core.load_settings(str(corrupt)) == core.DEFAULT_SETTINGS


# -- packaging --------------------------------------------------------------


def test_the_oxt_builds_with_the_layout_libreoffice_expects(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import build_oxt

    target = tmp_path / "odslint.oxt"
    build_oxt.build(target)

    with zipfile.ZipFile(target) as oxt:
        names = set(oxt.namelist())
        description = oxt.read("description.xml").decode()

    assert "META-INF/manifest.xml" in names
    assert "description.xml" in names
    assert "python/odslint_ext.py" in names
    # LibreOffice only puts a "pythonpath" dir beside the component on sys.path,
    # so a sibling module here would fail to import at registration time.
    assert "python/pythonpath/odslint_core.py" in names
    assert "config/Addons.xcu" in names
    assert "config/ProtocolHandler.xcu" in names

    from odslint import __version__

    assert f'<version value="{__version__}"/>' in description


def test_every_file_the_manifest_declares_is_in_the_package(tmp_path):
    """A manifest entry pointing at a file that is not there makes unopkg fail
    at install time with a message that names neither."""
    import re

    sys.path.insert(0, str(ROOT / "tools"))
    import build_oxt

    target = tmp_path / "odslint.oxt"
    build_oxt.build(target)
    with zipfile.ZipFile(target) as oxt:
        names = set(oxt.namelist())
        manifest = oxt.read("META-INF/manifest.xml").decode()

    declared = set(re.findall(r'manifest:full-path="([^"]+)"', manifest))
    assert declared
    assert declared <= names, f"declared but missing: {declared - names}"


def test_the_extension_python_has_no_syntax_errors():
    """It is loaded by LibreOffice's interpreter, never by the test run, so a
    syntax error would otherwise only surface as a silent failure to register."""
    for relative in ("odslint_ext.py", "pythonpath/odslint_core.py"):
        path = ROOT / "extension" / "python" / relative
        compile(path.read_text(encoding="utf-8"), relative, "exec")


def test_the_core_module_imports_nothing_from_uno_or_odslint():
    """It has to run under LibreOffice's Python, which has neither the project
    virtualenv nor lxml on its path."""
    source = (ROOT / "extension" / "python" / "pythonpath" / "odslint_core.py").read_text(
        encoding="utf-8"
    )
    assert "import uno" not in source
    assert "import odslint\n" not in source
    assert "from odslint" not in source


def test_the_license_shipped_in_the_extension_matches_the_project(tmp_path):
    assert (ROOT / "extension" / "registration" / "LICENSE").read_text() == (
        ROOT / "LICENSE"
    ).read_text()


def test_the_temp_file_filter_name_is_one_libreoffice_knows():
    """Verified against this machine's LibreOffice filter registry; a typo here
    makes storeToURL fail at runtime with an unhelpful message."""
    source = (ROOT / "extension" / "python" / "odslint_ext.py").read_text(encoding="utf-8")
    assert 'FLAT_FILTER = "OpenDocument Spreadsheet Flat XML"' in source
    registry = Path("/usr/lib/libreoffice/share/registry/calc.xcd")
    if registry.is_file():
        assert 'oor:name="OpenDocument Spreadsheet Flat XML"' in registry.read_text(
            encoding="utf-8", errors="replace"
        )


def test_odslint_is_importable_as_a_module_for_the_subprocess_path():
    """The extension shells out; ``python -m odslint`` is the fallback form."""
    result = subprocess.run(
        [sys.executable, "-m", "odslint", "--version"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ},
    )
    assert result.returncode == 0
    assert "odslint" in result.stdout
