"""End-to-end CLI behaviour: exit codes, formats, suppression, config."""

from __future__ import annotations

import json
import shutil

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
