"""End-to-end CLI behaviour: exit codes, formats, suppression, config."""

from __future__ import annotations

import json
import shutil

import pytest

from helpers import fixture
from odslint.cli import EXIT_ERROR, EXIT_FINDINGS, EXIT_OK, main


def test_findings_exit_with_one(capsys):
    assert main([str(fixture("magic_numbers.fods"))]) == EXIT_FINDINGS
    assert "formula/magic-number" in capsys.readouterr().out


def test_clean_file_exits_zero(tmp_path, capsys):
    from helpers import num, write_fods

    path = write_fods(tmp_path / "clean.fods", {"S": [[num(1), num(2)]]})
    assert main([str(path)]) == EXIT_OK
    assert "No problems found." in capsys.readouterr().out


def test_fail_on_never_still_reports(capsys):
    assert main([str(fixture("magic_numbers.fods")), "--fail-on", "never"]) == EXIT_OK
    assert "formula/magic-number" in capsys.readouterr().out


def test_fail_on_error_ignores_warnings(capsys):
    # magic-number defaults to warning, so an error threshold passes.
    assert main([str(fixture("magic_numbers.fods")), "--fail-on", "error"]) == EXIT_OK
    capsys.readouterr()


def test_json_output_is_machine_readable(capsys):
    main([str(fixture("text_numbers.fods")), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert {d["cell"] for d in payload} >= {"A2", "A4"}
    assert payload[0]["rule"] == "data/number-stored-as-text"
    assert payload[0]["severity"] == "error"


def test_rule_selection_runs_only_that_rule(capsys):
    main([str(fixture("named_ranges.fods")), "--rule", "formula/magic-number"])
    out = capsys.readouterr().out
    assert "formula/prefer-named-range" not in out


def test_unknown_rule_is_a_tool_error(capsys):
    assert main([str(fixture("named_ranges.fods")), "--rule", "nope/nope"]) == EXIT_ERROR
    assert "unknown rule" in capsys.readouterr().err


def test_unreadable_file_is_a_tool_error(tmp_path, capsys):
    broken = tmp_path / "broken.fods"
    broken.write_text("<not-odf/>", encoding="utf-8")
    assert main([str(broken)]) == EXIT_ERROR
    assert "odslint:" in capsys.readouterr().err


def test_missing_file_is_a_tool_error(tmp_path, capsys):
    assert main([str(tmp_path / "absent.ods")]) == EXIT_ERROR
    capsys.readouterr()


def test_list_rules(capsys):
    assert main(["--list-rules"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "formula/prefer-named-range" in out
    assert "data/number-stored-as-text" in out


def test_cell_annotations_suppress_diagnostics(capsys):
    main([str(fixture("suppression.fods")), "--fail-on", "never"])
    out = capsys.readouterr().out
    assert "Sheet1!C1" in out  # 1.07 is still reported
    assert "Sheet1!B1" not in out  # suppressed by rule id
    assert "Sheet1!A2" not in out  # suppressed by a bare directive
    assert "1 problem" in out


def test_config_is_discovered_and_applied(tmp_path, capsys):
    shutil.copy(fixture("magic_numbers.fods"), tmp_path / "book.fods")
    (tmp_path / ".odslintrc.toml").write_text(
        '[rules."formula/magic-number"]\nseverity = "off"\n', encoding="utf-8"
    )
    assert main([str(tmp_path / "book.fods")]) == EXIT_OK
    assert "No problems found." in capsys.readouterr().out


def test_no_config_ignores_a_discovered_file(tmp_path, capsys):
    shutil.copy(fixture("magic_numbers.fods"), tmp_path / "book.fods")
    (tmp_path / ".odslintrc.toml").write_text(
        '[rules."formula/magic-number"]\nseverity = "off"\n', encoding="utf-8"
    )
    assert main([str(tmp_path / "book.fods"), "--no-config"]) == EXIT_FINDINGS
    capsys.readouterr()


def test_severity_override_from_config(tmp_path, capsys):
    shutil.copy(fixture("magic_numbers.fods"), tmp_path / "book.fods")
    (tmp_path / ".odslintrc.toml").write_text(
        '[rules."formula/magic-number"]\nseverity = "info"\nallowed = [0, 1, -1]\n',
        encoding="utf-8",
    )
    main([str(tmp_path / "book.fods"), "--fail-on", "never"])
    assert "info [formula/magic-number]" in capsys.readouterr().out


def test_bad_config_is_a_tool_error(tmp_path, capsys):
    shutil.copy(fixture("magic_numbers.fods"), tmp_path / "book.fods")
    (tmp_path / ".odslintrc.toml").write_text(
        '[rules."formula/magic-number"]\nallowedd = [1]\n', encoding="utf-8"
    )
    assert main([str(tmp_path / "book.fods")]) == EXIT_ERROR
    assert "has no option" in capsys.readouterr().err


def test_unknown_rule_in_config_is_a_tool_error(tmp_path, capsys):
    shutil.copy(fixture("magic_numbers.fods"), tmp_path / "book.fods")
    (tmp_path / ".odslintrc.toml").write_text(
        '[rules."formula/nope"]\nseverity = "off"\n', encoding="utf-8"
    )
    assert main([str(tmp_path / "book.fods")]) == EXIT_ERROR
    assert "unknown rule" in capsys.readouterr().err


def test_multiple_files_are_linted_together(tmp_path, capsys):
    main(
        [
            str(fixture("magic_numbers.fods")),
            str(fixture("text_numbers.fods")),
            "--fail-on",
            "never",
        ]
    )
    out = capsys.readouterr().out
    assert "magic_numbers.fods" in out
    assert "text_numbers.fods" in out


# -- autofix ---------------------------------------------------------------


def _copy(tmp_path, name):
    target = tmp_path / name
    shutil.copy(fixture(name), target)
    return target


def test_fix_applies_the_safe_tier_and_reports_the_rest(tmp_path, capsys):
    path = _copy(tmp_path, "named_ranges.fods")
    assert main([str(path), "--fix"]) == EXIT_FINDINGS
    out = capsys.readouterr().out

    assert "Fixed 1 problem." in out
    # The fixed finding is gone; the ones with no mechanical fix remain.
    assert "already covers exactly that range" not in out
    assert "formula/magic-number" in out
    assert "of:=TaxRate*100" in path.read_text()


def test_fix_is_idempotent(tmp_path, capsys):
    path = _copy(tmp_path, "named_ranges.fods")
    main([str(path), "--fix"])
    capsys.readouterr()
    after_first = path.read_bytes()

    main([str(path), "--fix"])
    assert "Fixed" not in capsys.readouterr().out
    assert path.read_bytes() == after_first


def test_diff_previews_without_writing(tmp_path, capsys):
    path = _copy(tmp_path, "named_ranges.fods")
    before = path.read_bytes()

    assert main([str(path), "--diff"]) == EXIT_FINDINGS
    out = capsys.readouterr().out
    assert '-          <table:table-cell table:formula="of:=[.$B$1]*100"' in out
    assert '+          <table:table-cell table:formula="of:=TaxRate*100"' in out
    assert path.read_bytes() == before


def test_diff_on_a_file_with_nothing_to_fix_exits_zero(tmp_path, capsys):
    path = _copy(tmp_path, "magic_numbers.fods")
    assert main([str(path), "--diff"]) == EXIT_OK
    assert capsys.readouterr().out == ""


def test_fix_and_diff_together_are_rejected(tmp_path, capsys):
    path = _copy(tmp_path, "named_ranges.fods")
    with pytest.raises(SystemExit) as exc:
        main([str(path), "--fix", "--diff"])
    assert exc.value.code == EXIT_ERROR
    assert "mutually exclusive" in capsys.readouterr().err


def test_fixable_findings_are_marked_and_counted(capsys):
    main([str(fixture("named_ranges.fods"))])
    out = capsys.readouterr().out
    assert "[formula/prefer-named-range] [*]" in out
    assert "1 fixable with --fix" in out


def test_json_carries_the_fix_payload(capsys):
    main([str(fixture("named_ranges.fods")), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    fixes = [d["fix"] for d in payload if d["fix"]]
    assert len(fixes) == 1
    assert fixes[0]["applicability"] == "safe"
    assert fixes[0]["edits"] == [
        {
            "sheet": "Model",
            "row": 0,
            "column": 3,
            "kind": "formula",
            "formula": "=TaxRate*100",
            "formula_a1": "=TaxRate*100",
            "value": None,
            "text": None,
        }
    ]


def test_fix_restores_the_original_when_a_pass_fails(tmp_path, capsys, monkeypatch):
    """A fixer that leaves a spreadsheet unopenable is worse than one that does
    nothing, so a failure has to roll the file back."""
    from odslint import cli
    from odslint.fixer import FixError

    path = _copy(tmp_path, "named_ranges.fods")
    before = path.read_bytes()

    def explode(*args, **kwargs):
        path.write_bytes(b"wrecked")
        raise FixError("boom")

    monkeypatch.setattr(cli, "fix_file", explode)
    assert main([str(path), "--fix"]) == EXIT_ERROR
    assert path.read_bytes() == before
