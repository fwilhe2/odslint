"""Loading ``.ods`` (ZIP package) and ``.fods`` (flat XML) into the model.

The two packagings differ only in where the XML comes from; everything after
:func:`_parse_document` is shared, so no rule ever needs to know which it got.

The one thing to be careful about here is ``table:number-rows-repeated`` /
``table:number-columns-repeated``. A single row element may legally claim a
million rows, so repeats are never materialized blindly: empty runs only advance
the logical index, and content-carrying runs are capped at :data:`MAX_REPEAT`.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree

from odslint.model import (
    ERROR_VALUES,
    Cell,
    Document,
    NamedExpression,
    Sheet,
)

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "number": "urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0",
    "of": "urn:oasis:names:tc:opendocument:xmlns:of:1.2",
    "xlink": "http://www.w3.org/1999/xlink",
    "dc": "http://purl.org/dc/elements/1.1/",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "script": "urn:oasis:names:tc:opendocument:xmlns:script:1.0",
    "config": "urn:oasis:names:tc:opendocument:xmlns:config:1.0",
    "calcext": "urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0",
    "loext": "urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0",
}

#: Cap on how many copies of a *content-carrying* repeat run we materialize.
#: Empty runs are free (they only advance the index), so this only bites on
#: pathological files, and the truncation is reported in ``load_warnings``.
MAX_REPEAT = 1024

#: Hard ceiling matching Calc's own sheet limits, as a runaway guard.
MAX_ROWS = 1 << 20
MAX_COLS = 1 << 14

ODS_EXTENSIONS = frozenset({".ods", ".ots"})
FODS_EXTENSIONS = frozenset({".fods", ".fots", ".xml"})


def _q(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


TABLE_TABLE = _q("table", "table")
TABLE_ROW = _q("table", "table-row")
TABLE_CELL = _q("table", "table-cell")
COVERED_CELL = _q("table", "covered-table-cell")
NAMED_EXPRESSIONS = _q("table", "named-expressions")
NAMED_RANGE = _q("table", "named-range")
NAMED_EXPRESSION = _q("table", "named-expression")
TEXT_P = _q("text", "p")
TEXT_S = _q("text", "s")
TEXT_TAB = _q("text", "tab")
TEXT_LINE_BREAK = _q("text", "line-break")
OFFICE_ANNOTATION = _q("office", "annotation")

A_NAME = _q("table", "name")
A_STYLE_NAME = _q("table", "style-name")
A_ROWS_REPEATED = _q("table", "number-rows-repeated")
A_COLS_REPEATED = _q("table", "number-columns-repeated")
A_ROWS_SPANNED = _q("table", "number-rows-spanned")
A_COLS_SPANNED = _q("table", "number-columns-spanned")
A_FORMULA = _q("table", "formula")
A_VISIBILITY = _q("table", "visibility")
A_CELL_RANGE = _q("table", "cell-range-address")
A_BASE_CELL = _q("table", "base-cell-address")
A_EXPRESSION = _q("table", "expression")
A_VALUE_TYPE = _q("office", "value-type")
A_CALCEXT_VALUE_TYPE = _q("calcext", "value-type")
A_TEXT_C = _q("text", "c")

_VALUE_ATTRS = {
    "float": _q("office", "value"),
    "percentage": _q("office", "value"),
    "currency": _q("office", "value"),
    "date": _q("office", "date-value"),
    "time": _q("office", "time-value"),
    "boolean": _q("office", "boolean-value"),
    "string": _q("office", "string-value"),
}


class LoadError(Exception):
    """The file could not be read as an ODF spreadsheet."""


# -- shared traversal ------------------------------------------------------
#
# The loader materializes these runs into model cells; the fixer walks them to
# find the element behind one logical cell. Both need identical repeat and
# covered-cell arithmetic, and getting it wrong is *the* classic ODS parser bug,
# so there is exactly one implementation and both call it.


@dataclass(frozen=True)
class Run:
    """An element and the span of logical row/column indices it covers."""

    element: etree._Element
    index: int
    repeat: int

    @property
    def end(self) -> int:
        """One past the last index this run covers."""
        return self.index + self.repeat

    def covers(self, index: int) -> bool:
        return self.index <= index < self.end


def iter_row_runs(table: etree._Element) -> Iterator[Run]:
    """Row elements of a sheet with their logical row indices.

    ``iter`` rather than ``iterchildren``: rows may be nested in
    ``table:table-row-group`` / ``table:table-header-rows``, and document order
    is exactly the visual order.
    """
    index = 0
    for row_el in table.iter(TABLE_ROW):
        repeat = _int_attr(row_el, A_ROWS_REPEATED, 1)
        yield Run(row_el, index, repeat)
        index += repeat


def iter_cell_runs(row_el: etree._Element) -> Iterator[Run]:
    """Cell elements of a row with their logical column indices.

    ``table:covered-table-cell`` elements are included: they carry no content,
    but they consume column indices, and skipping them shifts every cell to
    their right one column left.
    """
    index = 0
    for cell_el in row_el.iterchildren(TABLE_CELL, COVERED_CELL):
        repeat = _int_attr(cell_el, A_COLS_REPEATED, 1)
        yield Run(cell_el, index, repeat)
        index += repeat


def load(path: str | Path) -> Document:
    """Load a spreadsheet from ``.ods`` or ``.fods``, detected by content."""
    path = Path(path)
    if not path.is_file():
        raise LoadError(f"not a file: {path}")

    if zipfile.is_zipfile(path):
        return _load_package(path)
    return _load_flat(path)


def _parse_bytes(data: bytes, what: str) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    try:
        return etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise LoadError(f"malformed XML in {what}: {exc}") from exc


def _load_package(path: Path) -> Document:
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        if "content.xml" not in names:
            raise LoadError(f"{path}: ZIP without content.xml — not an ODF package")
        content = _parse_bytes(zf.read("content.xml"), "content.xml")
        meta = _parse_bytes(zf.read("meta.xml"), "meta.xml") if "meta.xml" in names else None
        settings = (
            _parse_bytes(zf.read("settings.xml"), "settings.xml")
            if "settings.xml" in names
            else None
        )
    return _parse_document(path, content, meta, settings)


def _load_flat(path: Path) -> Document:
    root = _parse_bytes(path.read_bytes(), str(path))
    if root.tag != _q("office", "document"):
        raise LoadError(f"{path}: root element is {root.tag}, expected office:document")
    # In a flat file meta and settings are inlined under the same root.
    return _parse_document(path, root, root, root)


def _parse_document(
    path: Path,
    content: etree._Element,
    meta: etree._Element | None,
    settings: etree._Element | None,
) -> Document:
    body = content.find(f".//{{{NS['office']}}}spreadsheet")
    if body is None:
        raise LoadError(f"{path}: no office:spreadsheet body — not a spreadsheet document")

    doc = Document(path=path)
    hidden_styles = _hidden_table_styles(content)

    for index, table in enumerate(body.iterchildren(TABLE_TABLE)):
        doc.sheets.append(_parse_sheet(table, index, hidden_styles, doc.load_warnings))

    for container in body.iterchildren(NAMED_EXPRESSIONS):
        doc.named_expressions.extend(_parse_named_expressions(container, scope=None))

    if meta is not None:
        doc.metadata = _parse_meta(meta)
    if settings is not None:
        doc.settings = _parse_settings(settings)
    return doc


def _hidden_table_styles(content: etree._Element) -> set[str]:
    """Names of table styles that hide the sheet (``style:table-properties``)."""
    hidden: set[str] = set()
    for style in content.iter(_q("style", "style")):
        if style.get(_q("style", "family")) != "table":
            continue
        props = style.find(_q("style", "table-properties"))
        if props is not None and props.get(_q("table", "display")) == "false":
            name = style.get(_q("style", "name"))
            if name:
                hidden.add(name)
    return hidden


def _parse_sheet(
    table: etree._Element,
    index: int,
    hidden_styles: set[str],
    warnings: list[str],
) -> Sheet:
    name = table.get(A_NAME) or f"Sheet{index + 1}"
    sheet = Sheet(
        name=name,
        index=index,
        hidden=table.get(A_STYLE_NAME) in hidden_styles,
    )

    for run in iter_row_runs(table):
        if run.index >= MAX_ROWS:
            warnings.append(f"{name}: stopped at row {MAX_ROWS}")
            break
        cells = _parse_row(run.element, name, warnings)
        if not cells:
            continue
        copies = min(run.repeat, MAX_REPEAT)
        if copies < run.repeat:
            warnings.append(
                f"{name}: row {run.index + 1} repeats {run.repeat}x with content; "
                f"only the first {copies} were analyzed"
            )
        for offset in range(copies):
            for col, proto in cells:
                sheet.cells[(run.index + offset, col)] = Cell(
                    row=run.index + offset,
                    col=col,
                    value_type=proto.value_type,
                    value=proto.value,
                    text=proto.text,
                    formula=proto.formula,
                    error=proto.error,
                    annotations=proto.annotations,
                    rows_spanned=proto.rows_spanned,
                    cols_spanned=proto.cols_spanned,
                )

    for container in table.iterchildren(NAMED_EXPRESSIONS):
        sheet.named_expressions.extend(_parse_named_expressions(container, scope=name))

    return sheet


def _parse_row(
    row_el: etree._Element, sheet_name: str, warnings: list[str]
) -> list[tuple[int, Cell]]:
    """Non-empty cells of a row as ``(column, cell)``; empty runs are skipped."""
    out: list[tuple[int, Cell]] = []
    for run in iter_cell_runs(row_el):
        if run.index >= MAX_COLS:
            break
        cell = _parse_cell(run.element, 0, run.index)
        if cell.is_empty:
            # Includes covered cells of a merge and the trailing padding run
            # that every LibreOffice row ends with.
            continue
        copies = min(run.repeat, MAX_REPEAT)
        if copies < run.repeat:
            warnings.append(
                f"{sheet_name}: a cell repeats {run.repeat}x with content; "
                f"only the first {copies} were analyzed"
            )
        for offset in range(copies):
            out.append((run.index + offset, cell))
    return out


def _parse_cell(cell_el: etree._Element, row: int, col: int) -> Cell:
    value_type = cell_el.get(A_VALUE_TYPE)
    value = None
    if value_type is not None:
        attr = _VALUE_ATTRS.get(value_type)
        if attr is not None:
            value = cell_el.get(attr)

    text = _cell_text(cell_el)
    formula = cell_el.get(A_FORMULA)
    if formula is not None:
        eq = formula.find("=")
        formula = formula[eq:] if eq != -1 else f"={formula}"

    error = None
    if cell_el.get(A_CALCEXT_VALUE_TYPE) == "error" or text.strip() in ERROR_VALUES:
        error = text.strip() or "#ERR"

    annotations = tuple(
        "\n".join(_para_text(p) for p in ann.iter(TEXT_P))
        for ann in cell_el.iterchildren(OFFICE_ANNOTATION)
    )

    return Cell(
        row=row,
        col=col,
        value_type=value_type,
        value=value,
        text=text,
        formula=formula,
        error=error,
        annotations=annotations,
        rows_spanned=_int_attr(cell_el, A_ROWS_SPANNED, 1),
        cols_spanned=_int_attr(cell_el, A_COLS_SPANNED, 1),
    )


def _cell_text(cell_el: etree._Element) -> str:
    """Displayed text. Only direct ``text:p`` children — annotation text is not
    part of the cell value and must not leak in."""
    return "\n".join(_para_text(p) for p in cell_el.iterchildren(TEXT_P))


def _para_text(el: etree._Element) -> str:
    out = [el.text or ""]
    for child in el:
        if child.tag == TEXT_S:
            out.append(" " * _int_attr(child, A_TEXT_C, 1))
        elif child.tag == TEXT_TAB:
            out.append("\t")
        elif child.tag == TEXT_LINE_BREAK:
            out.append("\n")
        elif child.tag != OFFICE_ANNOTATION:
            out.append(_para_text(child))
        out.append(child.tail or "")
    return "".join(out)


def _parse_named_expressions(container: etree._Element, scope: str | None) -> list[NamedExpression]:
    from odslint.formula.reference import parse_range_address

    out: list[NamedExpression] = []
    for el in container:
        name = el.get(A_NAME)
        if not name:
            continue
        if el.tag == NAMED_RANGE:
            out.append(
                NamedExpression(
                    name=name,
                    scope=scope,
                    target=parse_range_address(el.get(A_CELL_RANGE) or ""),
                    base_cell=el.get(A_BASE_CELL),
                )
            )
        elif el.tag == NAMED_EXPRESSION:
            expression = el.get(A_EXPRESSION) or ""
            # LibreOffice stores plain rectangles as named-expression too when
            # they carry an of: prefix; recover the range where we can.
            target = None
            stripped = expression
            if ":=" in stripped:
                stripped = stripped.split(":=", 1)[1]
            if stripped.startswith("[") and stripped.endswith("]"):
                target = parse_range_address(stripped[1:-1])
            out.append(
                NamedExpression(
                    name=name,
                    scope=scope,
                    target=target,
                    expression=expression,
                    base_cell=el.get(A_BASE_CELL),
                )
            )
    return out


def _parse_meta(meta: etree._Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for el in meta.iter():
        tag = etree.QName(el).localname if isinstance(el.tag, str) else None
        ns = etree.QName(el).namespace if isinstance(el.tag, str) else None
        if ns in (NS["meta"], NS["dc"]) and el.text and tag:
            out[tag] = el.text
    return out


def _parse_settings(settings: etree._Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in settings.iter(_q("config", "config-item")):
        name = item.get(_q("config", "name"))
        if name and item.text is not None:
            out[name] = item.text
    return out


def _int_attr(el: etree._Element, attr: str, default: int) -> int:
    raw: Any = el.get(attr)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default
