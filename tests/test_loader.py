"""Loader tests. These carry the format's traps, so rule tests do not have to."""

from __future__ import annotations

import pytest

from helpers import fixture, fods_to_ods
from odslint.loader import LoadError, load


@pytest.fixture
def grid_doc():
    return load(fixture("repeats_and_merges.fods"))


def test_column_repeat_expands_to_logical_cells(grid_doc):
    grid = grid_doc.sheet("Grid")
    assert [grid.cell(0, col).text for col in range(3)] == ["x", "x", "x"]
    # The trailing padding run carries no content and must not become cells.
    assert grid.cell(0, 3) is None


def test_row_repeat_expands_to_logical_rows(grid_doc):
    grid = grid_doc.sheet("Grid")
    assert grid.cell(2, 0).number == 7
    assert grid.cell(3, 0).number == 7


def test_million_row_padding_is_not_materialized(grid_doc):
    grid = grid_doc.sheet("Grid")
    assert grid.used_range == (4, 2)
    assert len(grid.cells) == 8
    assert grid_doc.load_warnings == []


def test_covered_cells_keep_columns_aligned(grid_doc):
    grid = grid_doc.sheet("Grid")
    anchor = grid.cell(1, 0)
    assert anchor.cols_spanned == 2 and anchor.is_merged
    assert grid.cell(1, 1) is None  # the covered placeholder holds no content
    assert grid.cell(1, 2).text == "after"  # ... but still occupies its slot


def test_rows_inside_a_group_keep_document_order(grid_doc):
    assert grid_doc.sheet("Grid").cell(4, 0).number == 42


def test_annotation_text_stays_out_of_the_cell_value(grid_doc):
    cell = grid_doc.sheet("Grid").cell(4, 0)
    assert cell.text == "42"
    assert "odslint-disable formula/magic-number" in cell.annotations[0]


def test_hidden_sheet_detected_via_table_style(grid_doc):
    assert grid_doc.sheet("Hidden").hidden is True
    assert grid_doc.sheet("Grid").hidden is False


def test_named_range_resolves_to_a_rectangle():
    doc = load(fixture("named_ranges.fods"))
    (named,) = doc.named_expressions
    assert named.name == "TaxRate"
    assert str(named.target) == "Model!B1"
    assert named.scope is None


def test_formula_prefix_is_stripped():
    doc = load(fixture("magic_numbers.fods"))
    assert doc.sheet("Invoice").cell(1, 2).formula == "=[.B2]*1.19"


def test_ods_package_and_flat_file_agree(tmp_path):
    source = fixture("repeats_and_merges.fods")
    flat = load(source)
    packaged = load(fods_to_ods(source, tmp_path / "book.ods"))

    assert [s.name for s in packaged.sheets] == [s.name for s in flat.sheets]
    for a, b in zip(flat.sheets, packaged.sheets, strict=True):
        assert a.hidden == b.hidden
        assert sorted(a.cells) == sorted(b.cells)
        for key, cell in a.cells.items():
            assert cell.text == b.cells[key].text
            assert cell.formula == b.cells[key].formula


def test_rejects_a_zip_that_is_not_odf(tmp_path):
    import zipfile

    path = tmp_path / "not-odf.ods"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(LoadError, match="content.xml"):
        load(path)


def test_rejects_a_text_document(tmp_path):
    path = tmp_path / "doc.fods"
    path.write_text(
        '<?xml version="1.0"?><office:document '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0">'
        "<office:body/></office:document>",
        encoding="utf-8",
    )
    with pytest.raises(LoadError, match="not a spreadsheet"):
        load(path)
