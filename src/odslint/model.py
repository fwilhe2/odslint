"""The document model every rule works against.

The model is packaging-agnostic: a ``.ods`` ZIP and a flat ``.fods`` file both
load into exactly this shape (see :mod:`odslint.loader`).

Rows and columns are 0-based everywhere internally; only :func:`a1` and the
reporting layer speak the 1-based ``B7`` dialect users see in Calc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ERROR_VALUES = frozenset({"#REF!", "#DIV/0!", "#N/A", "#VALUE!", "#NAME?", "#NUM!", "#NULL!"})

#: Value types that represent a number-like literal rather than free text.
NUMERIC_TYPES = frozenset({"float", "percentage", "currency", "date", "time", "boolean"})


def col_letters(col: int) -> str:
    """0-based column index -> spreadsheet letters (0 -> ``A``, 26 -> ``AA``)."""
    if col < 0:
        raise ValueError(f"negative column index: {col}")
    out = ""
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def col_index(letters: str) -> int:
    """Spreadsheet letters -> 0-based column index. Inverse of :func:`col_letters`."""
    if not letters:
        raise ValueError("empty column letters")
    out = 0
    for ch in letters.upper():
        if not "A" <= ch <= "Z":
            raise ValueError(f"bad column letters: {letters!r}")
        out = out * 26 + (ord(ch) - ord("A") + 1)
    return out - 1


def a1(row: int, col: int) -> str:
    """0-based (row, col) -> ``B7``."""
    return f"{col_letters(col)}{row + 1}"


@dataclass(frozen=True)
class CellRange:
    """An absolute, sheet-qualified rectangle. Both corners are inclusive."""

    sheet: str
    row1: int
    col1: int
    row2: int
    col2: int

    @property
    def is_single_cell(self) -> bool:
        return self.row1 == self.row2 and self.col1 == self.col2

    def contains(self, sheet: str, row: int, col: int) -> bool:
        return (
            sheet.casefold() == self.sheet.casefold()
            and self.row1 <= row <= self.row2
            and self.col1 <= col <= self.col2
        )

    def __str__(self) -> str:
        start = a1(self.row1, self.col1)
        if self.is_single_cell:
            return f"{self.sheet}!{start}"
        return f"{self.sheet}!{start}:{a1(self.row2, self.col2)}"


@dataclass
class Cell:
    """A single logical cell.

    ``value`` holds the raw ODF attribute (``office:value``, ``office:date-value``,
    ...) and ``text`` the displayed string. For a formula cell both describe the
    *last calculated* result, which may be stale or an error — see ``error``.
    """

    row: int
    col: int
    value_type: str | None = None
    value: str | None = None
    text: str = ""
    formula: str | None = None
    error: str | None = None
    annotations: tuple[str, ...] = ()
    rows_spanned: int = 1
    cols_spanned: int = 1

    @property
    def is_empty(self) -> bool:
        return self.formula is None and self.value is None and not self.text

    @property
    def is_formula(self) -> bool:
        return self.formula is not None

    @property
    def is_merged(self) -> bool:
        return self.rows_spanned > 1 or self.cols_spanned > 1

    @property
    def number(self) -> float | None:
        """The numeric value, or ``None`` if this cell does not hold one."""
        if self.value is None or self.value_type not in NUMERIC_TYPES:
            return None
        try:
            return float(self.value)
        except ValueError:
            return None


@dataclass
class Sheet:
    name: str
    index: int
    hidden: bool = False
    #: Sparse: only cells with content are stored. Repeats are never materialized
    #: beyond what actually carries content (see loader.MAX_REPEAT).
    cells: dict[tuple[int, int], Cell] = field(default_factory=dict)
    named_expressions: list[NamedExpression] = field(default_factory=list)

    def cell(self, row: int, col: int) -> Cell | None:
        return self.cells.get((row, col))

    def iter_cells(self) -> list[Cell]:
        """Cells in row-major order."""
        return [self.cells[key] for key in sorted(self.cells)]

    def formula_cells(self) -> list[Cell]:
        return [c for c in self.iter_cells() if c.is_formula]

    @property
    def used_range(self) -> tuple[int, int] | None:
        """``(max_row, max_col)`` of the content, or ``None`` for an empty sheet."""
        if not self.cells:
            return None
        return (
            max(r for r, _ in self.cells),
            max(c for _, c in self.cells),
        )


@dataclass
class NamedExpression:
    """A ``table:named-range`` or ``table:named-expression``.

    ``scope`` is ``None`` for document scope, or a sheet name for a sheet-scoped
    name. Sheet-scoped names shadow document-scoped ones of the same name.
    """

    name: str
    scope: str | None = None
    #: Set when the name resolves to a plain rectangle (``table:named-range``).
    target: CellRange | None = None
    #: Raw ``table:expression`` for names that are not plain ranges.
    expression: str | None = None
    base_cell: str | None = None

    @property
    def is_range(self) -> bool:
        return self.target is not None


@dataclass
class Document:
    path: Path
    sheets: list[Sheet] = field(default_factory=list)
    #: Document-scoped names only; sheet-scoped ones live on their ``Sheet``.
    named_expressions: list[NamedExpression] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    settings: dict[str, str] = field(default_factory=dict)
    #: Non-fatal loader notes (e.g. a repeat run that hit the materialization cap).
    load_warnings: list[str] = field(default_factory=list)

    def sheet(self, name: str) -> Sheet | None:
        folded = name.casefold()
        for sheet in self.sheets:
            if sheet.name.casefold() == folded:
                return sheet
        return None

    def names_visible_from(self, sheet: Sheet) -> list[NamedExpression]:
        """Names resolvable from ``sheet``, sheet scope shadowing document scope."""
        local = {n.name.casefold(): n for n in sheet.named_expressions}
        out = list(local.values())
        out.extend(n for n in self.named_expressions if n.name.casefold() not in local)
        return out
