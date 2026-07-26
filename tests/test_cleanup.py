"""Flat-ODF cleanup: what it strips, what it must never change.

The property that matters is the last one — cleaning is churn removal, so a
cleaned fixture has to lint exactly like the original. Everything above it is
there to pin the individual removals that make that non-obvious.
"""

from __future__ import annotations

import shutil

import pytest

from helpers import FIXTURES, fixture, num, write_fods
from odslint.cleanup import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_WOULD_CHANGE,
    CleanupError,
    clean_bytes,
    clean_file,
    main,
)
from odslint.config import Config
from odslint.engine import lint_file, select_rules
from odslint.loader import load

ALL_FIXTURES = sorted(p.name for p in FIXTURES.glob("*.fods"))


def test_removes_unused_data_styles(tmp_path):
    path = write_fods(tmp_path / "book.fods", {"S": [[num(1)]]})
    original = path.read_text(encoding="utf-8")
    # LibreOffice emits a default set of number formats whether or not a cell
    # uses one; nothing here references N0, so it goes.
    with_style = original.replace(
        "  <office:body>",
        '  <office:automatic-styles>\n    <number:number-style style:name="N0"'
        ' xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0"'
        ' xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"/>\n'
        "  </office:automatic-styles>\n  <office:body>",
    )
    assert 'style:name="N0"' in with_style

    cleaned = clean_bytes(with_style.encode("utf-8")).decode("utf-8")
    assert 'style:name="N0"' not in cleaned


def test_removes_settings_scripts_and_volatile_meta(tmp_path):
    path = write_fods(tmp_path / "book.fods", {"S": [[num(1)]]})
    source = path.read_text(encoding="utf-8").replace(
        "  <office:body>",
        "  <office:meta"
        ' xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0">'
        "<meta:editing-cycles>7</meta:editing-cycles></office:meta>\n"
        "  <office:settings/>\n"
        '  <office:scripts xmlns:script="urn:oasis:names:tc:opendocument:xmlns:script:1.0"/>\n'
        "  <office:body>",
    )

    cleaned = clean_bytes(source.encode("utf-8")).decode("utf-8")
    assert "editing-cycles" not in cleaned
    assert "office:settings" not in cleaned
    assert "office:scripts" not in cleaned
    # office:meta held nothing but churn, so it goes too rather than being left
    # behind empty.
    assert "office:meta" not in cleaned


def test_keeps_the_of_namespace_that_formulas_reference_as_text():
    # xmlns:of is used only inside table:formula="of:=..." strings, so a
    # structural "is this prefix used?" analysis calls it dead. Dropping it makes
    # LibreOffice fail to resolve every formula in the file (Err:510).
    cleaned = clean_bytes(fixture("magic_numbers.fods").read_bytes()).decode("utf-8")
    assert "xmlns:of=" in cleaned
    assert 'table:formula="of:=' in cleaned


def test_preserves_comments_outside_the_root_element():
    # The fixtures explain themselves in a leading comment; upstream serializes
    # the root element alone and would drop it.
    cleaned = clean_bytes(fixture("repeats_and_merges.fods").read_bytes()).decode("utf-8")
    assert "Loader torture test" in cleaned
    assert cleaned.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!--')
    # ...and the root's start tag still begins a line, so its attributes get
    # split one per line like every other tag's.
    assert "\n<office:document\n xmlns:office=" in cleaned


def test_output_ends_with_a_newline():
    assert clean_bytes(fixture("magic_numbers.fods").read_bytes()).endswith(b">\n")


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_cleaning_is_idempotent(name):
    once = clean_bytes(fixture(name).read_bytes())
    assert clean_bytes(once) == once


def test_clean_file_reports_and_writes(tmp_path):
    path = shutil.copy(fixture("magic_numbers.fods"), tmp_path / "book.fods")
    before = path.read_bytes()
    assert clean_file(path) is True
    assert path.read_bytes() != before
    assert clean_file(path) is False


def test_check_mode_never_writes(tmp_path):
    path = shutil.copy(fixture("magic_numbers.fods"), tmp_path / "book.fods")
    before = path.read_bytes()
    assert clean_file(path, check=True) is True
    assert path.read_bytes() == before


def test_ods_package_is_rejected(tmp_path):
    from helpers import fods_to_ods

    package = fods_to_ods(fixture("magic_numbers.fods"), tmp_path / "book.ods")
    with pytest.raises(CleanupError, match="not flat XML"):
        clean_file(package)


def test_non_odf_xml_is_rejected(tmp_path):
    path = tmp_path / "other.fods"
    path.write_text("<not-odf/>", encoding="utf-8")
    with pytest.raises(CleanupError, match="expected office:document"):
        clean_file(path)


def test_malformed_xml_is_rejected():
    with pytest.raises(CleanupError, match="malformed XML"):
        clean_bytes(b"<office:document")


def test_cli_exit_codes(tmp_path, capsys):
    path = shutil.copy(fixture("magic_numbers.fods"), tmp_path / "book.fods")
    assert main([str(path), "--check"]) == EXIT_WOULD_CHANGE
    assert "would clean" in capsys.readouterr().out

    assert main([str(path)]) == EXIT_OK  # writing is success, not a finding
    assert "cleaned" in capsys.readouterr().out

    assert main([str(path), "--check"]) == EXIT_OK
    capsys.readouterr()

    assert main([str(tmp_path / "absent.fods")]) == EXIT_ERROR
    assert "odslint-clean:" in capsys.readouterr().err


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_cleaning_does_not_change_the_model_or_the_diagnostics(tmp_path, name):
    original = fixture(name)
    cleaned = shutil.copy(original, tmp_path / name)
    clean_file(cleaned)

    before = load(original)
    after = load(cleaned)
    assert [s.name for s in after.sheets] == [s.name for s in before.sheets]
    for expected, actual in zip(before.sheets, after.sheets, strict=True):
        # Renumbering the automatic table styles rewrites the references too,
        # so even a hidden sheet has to stay hidden.
        assert actual.hidden == expected.hidden
        assert sorted(actual.cells) == sorted(expected.cells), f"{name}: cell grid moved"
        for key, cell in expected.cells.items():
            other = actual.cells[key]
            assert other.formula == cell.formula
            assert other.value == cell.value
            assert other.text == cell.text
            assert other.annotations == cell.annotations
            assert other.cols_spanned == cell.cols_spanned

    config = Config()
    rules = select_rules(config)

    def signature(path):
        return [(d.rule_id, d.location, d.message) for d in lint_file(path, config, rules)]

    assert signature(cleaned) == signature(original)
