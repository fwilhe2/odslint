"""Fixer tests.

The interesting cases are all structural: a repeated run has to be split so an
edit lands on one cell and not on its neighbours, and a package has to come back
out with every part it went in with. ``repeats_and_merges.fods`` is the fixture
that encodes the traps, so most of this runs against it.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from helpers import fixture, fods_to_ods
from odslint import package
from odslint.diagnostics import EDIT_FORMULA, EDIT_NUMBER, Applicability, Diagnostic, Edit, Fix
from odslint.fixer import FixError, fix_file, plan_fixes, preview
from odslint.loader import load


def _grid(tmp_path: Path) -> Path:
    target = tmp_path / "repeats_and_merges.fods"
    shutil.copy(fixture("repeats_and_merges.fods"), target)
    return target


def formula_edit(row: int, col: int, expression: str, sheet: str = "Grid") -> Edit:
    return Edit(sheet=sheet, row=row, col=col, kind=EDIT_FORMULA, formula=expression)


# -- splitting repeated runs ------------------------------------------------


def test_editing_one_cell_of_a_column_repeat_leaves_its_neighbours_alone(tmp_path: Path) -> None:
    """Row 1 is a single element claiming three columns. Editing B1 must not
    touch A1 or C1, and must not shift C1 leftwards."""
    path = _grid(tmp_path)
    assert fix_file(path, [formula_edit(0, 1, "=1+1")])

    sheet = load(path).sheet("Grid")
    assert sheet is not None
    assert sheet.cell(0, 0) is not None and sheet.cell(0, 0).text == "x"
    assert sheet.cell(0, 1) is not None and sheet.cell(0, 1).formula == "=1+1"
    assert sheet.cell(0, 2) is not None and sheet.cell(0, 2).text == "x"
    # The run was exactly three wide; nothing may appear past it.
    assert sheet.cell(0, 3) is None


def test_editing_the_first_cell_of_a_repeat_needs_no_leading_copy(tmp_path: Path) -> None:
    path = _grid(tmp_path)
    assert fix_file(path, [formula_edit(0, 0, "=2*2")])

    sheet = load(path).sheet("Grid")
    assert sheet is not None
    assert sheet.cell(0, 0) is not None and sheet.cell(0, 0).formula == "=2*2"
    assert sheet.cell(0, 1) is not None and sheet.cell(0, 1).text == "x"
    assert sheet.cell(0, 2) is not None and sheet.cell(0, 2).text == "x"
    assert sheet.cell(0, 3) is None


def test_editing_the_last_cell_of_a_repeat_needs_no_trailing_copy(tmp_path: Path) -> None:
    path = _grid(tmp_path)
    assert fix_file(path, [formula_edit(0, 2, "=3*3")])

    sheet = load(path).sheet("Grid")
    assert sheet is not None
    assert sheet.cell(0, 0) is not None and sheet.cell(0, 0).text == "x"
    assert sheet.cell(0, 1) is not None and sheet.cell(0, 1).text == "x"
    assert sheet.cell(0, 2) is not None and sheet.cell(0, 2).formula == "=3*3"
    assert sheet.cell(0, 3) is None


def test_editing_one_row_of_a_row_repeat_leaves_the_other_alone(tmp_path: Path) -> None:
    """Rows 3-4 are one element with number-rows-repeated="2"."""
    path = _grid(tmp_path)
    assert fix_file(path, [formula_edit(3, 0, "=9")])

    sheet = load(path).sheet("Grid")
    assert sheet is not None
    assert sheet.cell(2, 0) is not None and sheet.cell(2, 0).value == "7"
    assert sheet.cell(3, 0) is not None and sheet.cell(3, 0).formula == "=9"
    # Row 5 lives in a row group after the repeat; its index must not move.
    assert sheet.cell(4, 0) is not None and sheet.cell(4, 0).value == "42"


def test_a_cell_after_a_merge_is_found_at_its_shifted_column(tmp_path: Path) -> None:
    """A2:B2 is merged, so "after" sits at column index 2 behind a
    covered-table-cell that consumes index 1."""
    path = _grid(tmp_path)
    assert fix_file(path, [formula_edit(1, 2, "=42")])

    sheet = load(path).sheet("Grid")
    assert sheet is not None
    assert sheet.cell(1, 0) is not None and sheet.cell(1, 0).text == "merged"
    assert sheet.cell(1, 2) is not None and sheet.cell(1, 2).formula == "=42"


def test_editing_a_covered_cell_is_refused(tmp_path: Path) -> None:
    path = _grid(tmp_path)
    with pytest.raises(FixError, match="covered by a merge"):
        fix_file(path, [formula_edit(1, 1, "=1")])


def test_a_cell_inside_a_row_group_is_reachable(tmp_path: Path) -> None:
    """Rows nest in table:table-row-group, so the walk cannot use iterchildren."""
    path = _grid(tmp_path)
    assert fix_file(path, [formula_edit(4, 0, "=6*7")])

    sheet = load(path).sheet("Grid")
    assert sheet is not None
    cell = sheet.cell(4, 0)
    assert cell is not None and cell.formula == "=6*7"
    # The annotation is a sibling of the text, and must survive the edit.
    assert cell.annotations


def test_edits_to_several_cells_of_one_repeat_compose(tmp_path: Path) -> None:
    path = _grid(tmp_path)
    assert fix_file(path, [formula_edit(0, 0, "=1"), formula_edit(0, 2, "=3")])

    sheet = load(path).sheet("Grid")
    assert sheet is not None
    assert sheet.cell(0, 0) is not None and sheet.cell(0, 0).formula == "=1"
    assert sheet.cell(0, 1) is not None and sheet.cell(0, 1).text == "x"
    assert sheet.cell(0, 2) is not None and sheet.cell(0, 2).formula == "=3"
    assert sheet.cell(0, 3) is None


# -- applying edits ---------------------------------------------------------


def test_a_number_edit_retypes_the_cell_and_rewrites_its_text(tmp_path: Path) -> None:
    path = tmp_path / "text_numbers.fods"
    shutil.copy(fixture("text_numbers.fods"), path)
    doc = load(path)
    sheet = doc.sheets[0]
    target = next(c for c in sheet.iter_cells() if c.value_type == "string" and c.text.strip())

    edit = Edit(
        sheet=sheet.name,
        row=target.row,
        col=target.col,
        kind=EDIT_NUMBER,
        value="12.5",
        text="12.5",
    )
    assert fix_file(path, [edit])

    fixed = load(path).sheets[0].cell(target.row, target.col)
    assert fixed is not None
    assert fixed.value_type == "float"
    assert fixed.value == "12.5"
    assert fixed.text == "12.5"
    assert fixed.number == 12.5


def test_an_edit_naming_an_unknown_sheet_is_refused(tmp_path: Path) -> None:
    path = _grid(tmp_path)
    with pytest.raises(FixError, match="no sheet named"):
        fix_file(path, [formula_edit(0, 0, "=1", sheet="Nope")])


def test_sheets_are_matched_case_insensitively_like_the_model(tmp_path: Path) -> None:
    path = _grid(tmp_path)
    assert fix_file(path, [formula_edit(0, 0, "=1", sheet="GRID")])


def test_no_edits_writes_nothing(tmp_path: Path) -> None:
    path = _grid(tmp_path)
    before = path.read_bytes()
    assert fix_file(path, []) is False
    assert path.read_bytes() == before


def test_a_fixed_flat_file_still_loads_and_only_changes_the_target(tmp_path: Path) -> None:
    path = _grid(tmp_path)
    before = load(path)
    fix_file(path, [formula_edit(0, 1, "=1+1")])
    after = load(path)

    assert [s.name for s in before.sheets] == [s.name for s in after.sheets]
    for old_sheet, new_sheet in zip(before.sheets, after.sheets, strict=True):
        assert old_sheet.cells.keys() == new_sheet.cells.keys()
        assert old_sheet.hidden == new_sheet.hidden


# -- packages ---------------------------------------------------------------


def test_fixing_a_package_preserves_every_other_entry(tmp_path: Path) -> None:
    ods = fods_to_ods(fixture("repeats_and_merges.fods"), tmp_path / "book.ods")
    with zipfile.ZipFile(ods) as zf:
        before = {i.filename: zf.read(i.filename) for i in zf.infolist()}

    assert fix_file(ods, [formula_edit(0, 1, "=1+1")])

    with zipfile.ZipFile(ods) as zf:
        after = {i.filename: zf.read(i.filename) for i in zf.infolist()}

    assert before.keys() == after.keys()
    for name in before:
        if name != package.CONTENT:
            assert before[name] == after[name], f"{name} was not preserved"
    assert before[package.CONTENT] != after[package.CONTENT]


def test_a_fixed_package_keeps_mimetype_first_and_stored(tmp_path: Path) -> None:
    """LibreOffice rejects a package whose first entry is not a stored mimetype."""
    ods = fods_to_ods(fixture("repeats_and_merges.fods"), tmp_path / "book.ods")
    fix_file(ods, [formula_edit(0, 1, "=1+1")])

    with zipfile.ZipFile(ods) as zf:
        first = zf.infolist()[0]
    assert first.filename == "mimetype"
    assert first.compress_type == zipfile.ZIP_STORED


def test_a_fixed_package_reloads_with_the_edit_applied(tmp_path: Path) -> None:
    ods = fods_to_ods(fixture("repeats_and_merges.fods"), tmp_path / "book.ods")
    fix_file(ods, [formula_edit(0, 1, "=1+1")])

    sheet = load(ods).sheet("Grid")
    assert sheet is not None
    assert sheet.cell(0, 1) is not None and sheet.cell(0, 1).formula == "=1+1"
    assert sheet.cell(0, 2) is not None and sheet.cell(0, 2).text == "x"


def test_preview_labels_the_package_part_it_would_rewrite(tmp_path: Path) -> None:
    ods = fods_to_ods(fixture("repeats_and_merges.fods"), tmp_path / "book.ods")
    before, after, label = preview(ods, [formula_edit(0, 1, "=1+1")])
    assert label.endswith(":content.xml")
    assert before != after
    # A preview must not write.
    assert package.read_part(ods) == before


# -- attribute layout -------------------------------------------------------


def _is_split(data: bytes) -> bool:
    """Whether every multi-attribute start tag already sits one per line."""
    from odslint.vendor.flat_odf_cleanup import split_attributes_onto_lines

    return bool(split_attributes_onto_lines(data) == data)


def test_a_cleaned_file_keeps_one_attribute_per_line(tmp_path: Path) -> None:
    """odslint-clean splits start tags; lxml would collapse them again, so the
    fixer puts them back when — and only when — the file already had them."""
    from odslint.cleanup import clean_file

    path = _grid(tmp_path)
    clean_file(path)
    assert _is_split(path.read_bytes()), "cleaning should leave the file in split form"

    fix_file(path, [formula_edit(0, 1, "=1+1")])
    after = path.read_bytes()
    assert b"table:formula" in after
    assert _is_split(after), "the fixer collapsed a cleaned file's start tags"


def test_an_unsplit_file_is_not_gratuitously_split(tmp_path: Path) -> None:
    """The fixture writes several attributes per line; fixing it must not
    reformat every start tag in the document."""
    path = _grid(tmp_path)
    assert not _is_split(path.read_bytes())

    fix_file(path, [formula_edit(0, 1, "=1+1")])
    assert not _is_split(path.read_bytes())


def test_a_fixed_file_still_cleans_and_lints_the_same(tmp_path: Path) -> None:
    """The cleanup contract — a cleaned file lints identically — has to keep
    holding after a fix, in either order."""
    from odslint.cleanup import clean_file
    from odslint.config import Config
    from odslint.engine import lint_file

    fix_then_clean = _grid(tmp_path)
    fix_file(fix_then_clean, [formula_edit(0, 1, "=1+1")])
    clean_file(fix_then_clean)

    clean_then_fix = tmp_path / "other.fods"
    shutil.copy(fixture("repeats_and_merges.fods"), clean_then_fix)
    clean_file(clean_then_fix)
    fix_file(clean_then_fix, [formula_edit(0, 1, "=1+1")])

    config = Config()
    left = [(d.location, d.rule_id) for d in lint_file(fix_then_clean, config)]
    right = [(d.location, d.rule_id) for d in lint_file(clean_then_fix, config)]
    assert left == right


# -- planning ---------------------------------------------------------------


def _diag(rule: str, row: int, col: int, applicability: Applicability) -> Diagnostic:
    return Diagnostic(
        rule_id=rule,
        sheet="Grid",
        message="x",
        row=row,
        col=col,
        fix=Fix(
            title="t",
            applicability=applicability,
            edits=(formula_edit(row, col, "=1"),),
        ),
    )


def test_planning_skips_unsafe_fixes_unless_asked() -> None:
    diagnostics = [
        _diag("a/one", 0, 0, Applicability.SAFE),
        _diag("a/two", 1, 0, Applicability.UNSAFE),
    ]
    assert len(plan_fixes(diagnostics).edits) == 1
    assert len(plan_fixes(diagnostics, unsafe=True).edits) == 2


def test_planning_lets_the_first_fix_claim_a_cell_and_counts_the_rest() -> None:
    diagnostics = [
        _diag("a/one", 0, 0, Applicability.SAFE),
        _diag("a/two", 0, 0, Applicability.SAFE),
    ]
    plan = plan_fixes(diagnostics)
    assert len(plan.edits) == 1
    assert plan.conflicts == 1
    assert [d.rule_id for d in plan.fixed] == ["a/one"]


def test_planning_ignores_diagnostics_without_a_fix() -> None:
    plain = Diagnostic(rule_id="a/none", sheet="Grid", message="x", row=0, col=0)
    assert plan_fixes([plain]).is_empty
