"""Applying :class:`~odslint.diagnostics.Edit` objects to a spreadsheet file.

This is the downstream half of autofix. Rules describe edits; nothing here knows
which rule produced one, and nothing here consults a diagnostic. The same
:class:`Edit` objects are replayed through UNO by the LibreOffice extension
against a document open in Calc — that is the whole reason a fix is data rather
than a patch.

Two things make this harder than "find the cell and set an attribute":

**Nothing points back at the XML.** The model keeps no lxml reference, and one
element legitimately stands for many logical cells, so the fixer re-parses and
re-locates by ``(sheet, row, col)`` using the loader's own traversal.

**Repeats have to be split.** Editing one cell inside
``table:number-columns-repeated="5"`` means breaking that element into up to
three, so the change lands on the target cell and not on its four neighbours.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from odslint import package
from odslint.cleanup import assemble
from odslint.diagnostics import EDIT_FORMULA, EDIT_NUMBER, Diagnostic, Edit
from odslint.loader import (
    _VALUE_ATTRS,
    A_CALCEXT_VALUE_TYPE,
    A_COLS_REPEATED,
    A_FORMULA,
    A_NAME,
    A_ROWS_REPEATED,
    A_VALUE_TYPE,
    TABLE_CELL,
    TABLE_TABLE,
    TEXT_P,
    _q,
    iter_cell_runs,
    iter_row_runs,
)
from odslint.vendor import flat_odf_cleanup as _vendor

A_OFFICE_VALUE = _q("office", "value")
OFFICE_SPREADSHEET = _q("office", "spreadsheet")


class FixError(Exception):
    """An edit could not be applied to the document."""


# -- planning --------------------------------------------------------------


@dataclass
class Plan:
    """The edits to apply, and the fixes that were left alone."""

    edits: list[Edit] = field(default_factory=list)
    fixed: list[Diagnostic] = field(default_factory=list)
    conflicts: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.edits


def plan_fixes(diagnostics: Iterable[Diagnostic], *, unsafe: bool = False) -> Plan:
    """Choose which fixes to apply.

    One cell can only be rewritten once per run, so where two diagnostics want
    the same cell the first in report order wins and the rest are counted as
    conflicts — running again picks up whatever is still outstanding.
    """
    plan = Plan()
    claimed: set[tuple[str, int, int]] = set()

    for diagnostic in diagnostics:
        fix = diagnostic.fix
        if fix is None or not fix.edits:
            continue
        if not fix.is_safe and not unsafe:
            continue
        targets = {edit.target for edit in fix.edits}
        if targets & claimed:
            plan.conflicts += 1
            continue
        claimed |= targets
        plan.edits.extend(fix.edits)
        plan.fixed.append(diagnostic)

    return plan


# -- locating a cell -------------------------------------------------------


def _sheet_elements(root: etree._Element) -> dict[str, etree._Element]:
    """Sheet name -> table element, keyed the way the model looks sheets up."""
    body = root.find(f".//{OFFICE_SPREADSHEET}")
    if body is None:
        raise FixError("no office:spreadsheet body")
    out: dict[str, etree._Element] = {}
    for index, table in enumerate(body.iterchildren(TABLE_TABLE)):
        name = table.get(A_NAME) or f"Sheet{index + 1}"
        out.setdefault(name.casefold(), table)
    return out


def _set_repeat(el: etree._Element, attr: str, count: int) -> None:
    if count > 1:
        el.set(attr, str(count))
    else:
        el.attrib.pop(attr, None)


def _split_run(element: etree._Element, start: int, repeat: int, index: int, attr: str) -> None:
    """Break a repeated run so that ``index`` is covered by ``element`` alone.

    The run becomes up to three siblings — the part before ``index``, the target
    itself, and the part after — with the repeat counts adding back up to the
    original. ``deepcopy`` carries the tail whitespace along, so the copies sit
    in the file the way the original did.
    """
    if repeat == 1:
        return

    parent = element.getparent()
    if parent is None:
        raise FixError("cannot split a repeated run with no parent element")

    before = index - start
    after = start + repeat - index - 1
    head = deepcopy(element) if before else None
    tail = deepcopy(element) if after else None

    at = parent.index(element)
    if head is not None:
        _set_repeat(head, attr, before)
        parent.insert(at, head)
        at += 1
    if tail is not None:
        _set_repeat(tail, attr, after)
        parent.insert(at + 1, tail)
    _set_repeat(element, attr, 1)


def locate_cell(table: etree._Element, row: int, col: int) -> etree._Element:
    """The ``table:table-cell`` element for one logical cell, exclusively.

    Splits repeated rows and columns as needed, so the returned element stands
    for this cell and no other.
    """
    row_run = next((run for run in iter_row_runs(table) if run.covers(row)), None)
    if row_run is None:
        raise FixError(f"row {row + 1} does not exist in the sheet")
    _split_run(row_run.element, row_run.index, row_run.repeat, row, A_ROWS_REPEATED)
    row_el = row_run.element

    cell_run = next((run for run in iter_cell_runs(row_el) if run.covers(col)), None)
    if cell_run is None:
        raise FixError(f"column {col + 1} does not exist in row {row + 1}")
    if cell_run.element.tag != TABLE_CELL:
        # A covered cell of a merge carries no content and the model never
        # yields one, so an edit aimed at it means the coordinates are wrong.
        raise FixError(f"cell at row {row + 1}, column {col + 1} is covered by a merge")
    _split_run(cell_run.element, cell_run.index, cell_run.repeat, col, A_COLS_REPEATED)
    return cell_run.element


# -- applying one edit -----------------------------------------------------


def _set_display_text(cell_el: etree._Element, text: str) -> None:
    """Replace the cell's displayed text, leaving annotations in place."""
    paragraphs = list(cell_el.iterchildren(TEXT_P))
    if not paragraphs:
        etree.SubElement(cell_el, TEXT_P).text = text
        return
    first = paragraphs[0]
    for child in list(first):
        first.remove(child)
    first.text = text
    for extra in paragraphs[1:]:
        cell_el.remove(extra)


def apply_edit(cell_el: etree._Element, edit: Edit) -> None:
    if edit.kind == EDIT_FORMULA:
        if not edit.formula:
            raise FixError("a formula edit carries no formula")
        # ``table:formula`` is namespace-prefixed in its own value, and the
        # prefix has to be there textually or LibreOffice reads the whole thing
        # as a literal.
        formula = edit.formula
        cell_el.set(A_FORMULA, formula if formula.startswith("of:") else f"of:{formula}")
        # The cached ``office:value`` is now the result of the *old* formula.
        # It stays: a stored value being stale is a normal condition in ODF
        # rather than a corruption, Calc recalculates on load (verified), and
        # deleting it would make the cell read as empty in the simpler readers
        # that never recalculate. Anything reading cached values still wants a
        # recalculation pass after an unsafe fix.
        return

    if edit.kind == EDIT_NUMBER:
        if edit.value is None:
            raise FixError("a number edit carries no value")
        for attr in set(_VALUE_ATTRS.values()):
            cell_el.attrib.pop(attr, None)
        cell_el.set(A_VALUE_TYPE, "float")
        cell_el.set(A_OFFICE_VALUE, edit.value)
        if A_CALCEXT_VALUE_TYPE in cell_el.attrib:
            cell_el.set(A_CALCEXT_VALUE_TYPE, "float")
        _set_display_text(cell_el, edit.text if edit.text is not None else edit.value)
        return

    raise FixError(f"unknown edit kind: {edit.kind!r}")


def rewrite(xml: bytes, edits: Sequence[Edit]) -> bytes:
    """Apply edits to a serialized ``content.xml`` or flat document.

    Works on either because both put the sheets under ``office:spreadsheet``.
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    try:
        root = etree.fromstring(xml, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise FixError(f"malformed XML: {exc}") from exc

    sheets = _sheet_elements(root)
    for edit in edits:
        table = sheets.get(edit.sheet.casefold())
        if table is None:
            raise FixError(f"no sheet named {edit.sheet!r}")
        apply_edit(locate_cell(table, edit.row, edit.col), edit)

    body: bytes = etree.tostring(root, encoding="UTF-8", xml_declaration=False)
    return assemble(root, body)


def _keeps_split_attributes(original: bytes) -> bool:
    """Whether the file already writes one attribute per line.

    lxml preserves the whitespace *between* elements but not the whitespace
    *inside* a start tag, so re-serializing a file that has been through
    ``odslint-clean`` would collapse every start tag it worked so hard to split.
    Running the same cosmetic pass over the output puts them back; running it
    over a file that never had it would be gratuitous churn, hence the test.
    """
    split: bytes = _vendor.split_attributes_onto_lines(original)  # type: ignore[no-untyped-call]
    return split == original


# -- file level ------------------------------------------------------------


def preview(path: str | Path, edits: Sequence[Edit]) -> tuple[bytes, bytes, str]:
    """``(before, after, label)`` for the text a ``--diff`` should show.

    For a flat file that is the whole document; for a package it is
    ``content.xml``, the only part a fix ever touches.
    """
    path = Path(path)
    if package.is_package(path):
        before = package.read_part(path)
        return before, rewrite(before, edits), f"{path}:{package.CONTENT}"
    before = path.read_bytes()
    after = rewrite(before, edits)
    if _keeps_split_attributes(before):
        after = _vendor.split_attributes_onto_lines(after)  # type: ignore[no-untyped-call]
    return before, after, str(path)


def fix_file(path: str | Path, edits: Sequence[Edit]) -> bool:
    """Apply edits to the file in place. Returns whether anything was written."""
    if not edits:
        return False
    path = Path(path)
    _, after, _ = preview(path, edits)
    if package.is_package(path):
        package.replace_part(path, after)
    else:
        path.write_bytes(after)
    return True
