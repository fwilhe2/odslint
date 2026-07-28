"""Parsing of the bracketed reference syntax used inside OpenFormula.

Shapes handled (the text between the brackets is what gets passed in here)::

    .A1                      same sheet, relative
    .$B$7                    same sheet, absolute
    Sheet2.A1                other sheet
    $'Sheet Name'.$A$1       quoted sheet name, '' escapes a literal quote
    .A1:.B2                  range
    .A:.B                    whole-column range (no row component)
    'file:///x.ods'#$S.A1    external
    #REF!                    a reference that no longer resolves
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from odslint.model import CellRange, col_index, col_letters

_PART_RE = re.compile(
    r"""
    ^
    (?:(?P<sheet_abs>\$)?(?P<sheet>'(?:[^']|'')*'|[^.'\[\]:]*))?
    \.
    (?P<col_abs>\$?)(?P<col>[A-Za-z]*)
    (?P<row_abs>\$?)(?P<row>[0-9]*)
    $
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class RefPart:
    """One corner of a reference. ``row``/``col`` are 0-based, ``None`` if absent."""

    sheet: str | None
    sheet_abs: bool
    col: int | None
    col_abs: bool
    row: int | None
    row_abs: bool
    #: Whether the sheet name was written ``'quoted'``. ``sheet`` always holds
    #: the unquoted name, so writing one back out needs this to round-trip.
    sheet_quoted: bool = False

    @property
    def is_complete(self) -> bool:
        return self.row is not None and self.col is not None


@dataclass(frozen=True)
class Reference:
    """A parsed ``[...]`` reference."""

    raw: str
    start: RefPart | None = None
    end: RefPart | None = None
    external: str | None = None
    invalid: bool = False

    @property
    def is_range(self) -> bool:
        return self.end is not None

    @property
    def is_single_cell(self) -> bool:
        return self.end is None and self.start is not None and self.start.is_complete

    @property
    def is_absolute(self) -> bool:
        """True when every present component is anchored with ``$``."""
        parts = [p for p in (self.start, self.end) if p is not None]
        if not parts:
            return False
        return all((p.col is None or p.col_abs) and (p.row is None or p.row_abs) for p in parts)

    @property
    def sheet(self) -> str | None:
        return self.start.sheet if self.start else None

    @property
    def is_whole_column(self) -> bool:
        parts = [p for p in (self.start, self.end) if p is not None]
        return bool(parts) and all(p.col is not None and p.row is None for p in parts)


def _unquote_sheet(text: str) -> str:
    if text.startswith("'") and text.endswith("'") and len(text) >= 2:
        return text[1:-1].replace("''", "'")
    return text


def _split_top_level(text: str, sep: str) -> list[str]:
    """Split on ``sep``, ignoring separators inside ``'...'`` quoting."""
    out: list[str] = []
    buf: list[str] = []
    i = 0
    in_quote = False
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if in_quote and text[i : i + 2] == "''":
                buf.append("''")
                i += 2
                continue
            in_quote = not in_quote
            buf.append(ch)
        elif ch == sep and not in_quote:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    out.append("".join(buf))
    return out


def parse_ref_part(text: str) -> RefPart | None:
    match = _PART_RE.match(text)
    if match is None:
        return None
    sheet_raw = match.group("sheet") or ""
    col_text = match.group("col")
    row_text = match.group("row")
    if not col_text and not row_text:
        return None
    try:
        col = col_index(col_text) if col_text else None
    except ValueError:
        return None
    return RefPart(
        sheet=_unquote_sheet(sheet_raw) if sheet_raw else None,
        sheet_abs=bool(match.group("sheet_abs")),
        col=col,
        col_abs=bool(match.group("col_abs")),
        row=int(row_text) - 1 if row_text else None,
        row_abs=bool(match.group("row_abs")),
        sheet_quoted=sheet_raw.startswith("'"),
    )


def parse_reference(inner: str) -> Reference:
    """Parse the contents of a ``[...]`` token. Never raises."""
    raw = inner
    external: str | None = None
    body = inner

    if "#" in body:
        head, _, tail = body.partition("#")
        if tail.upper().startswith("REF!") or head == "":
            # A dead reference: [#REF!], [#REF!.A1], [.A1:#REF!] ...
            return Reference(raw=raw, invalid=True)
        external = _unquote_sheet(head)
        body = tail
    if "#REF!" in body.upper():
        return Reference(raw=raw, invalid=True)

    # External refs anchor the sheet with a leading $ that is not an abs marker
    # in the usual sense; parse_ref_part tolerates it either way.
    parts = _split_top_level(body, ":")
    if len(parts) == 1:
        start = parse_ref_part(parts[0])
        return Reference(raw=raw, start=start, external=external, invalid=start is None)
    if len(parts) == 2:
        start = parse_ref_part(parts[0])
        end = parse_ref_part(parts[1])
        return Reference(
            raw=raw,
            start=start,
            end=end,
            external=external,
            invalid=start is None or end is None,
        )
    return Reference(raw=raw, invalid=True)


def resolve(ref: Reference, current_sheet: str) -> CellRange | None:
    """Absolute rectangle for ``ref``, or ``None`` if it cannot be pinned down.

    Returns ``None`` for external, invalid, and partial (whole-row/column)
    references — callers should treat that as "not analyzable", not as an error.
    """
    if ref.invalid or ref.external is not None or ref.start is None:
        return None
    if not ref.start.is_complete:
        return None
    end = ref.end if ref.end is not None else ref.start
    if not end.is_complete:
        return None
    sheet = ref.start.sheet or current_sheet
    assert ref.start.row is not None and ref.start.col is not None
    assert end.row is not None and end.col is not None
    return CellRange(
        sheet=sheet,
        row1=min(ref.start.row, end.row),
        col1=min(ref.start.col, end.col),
        row2=max(ref.start.row, end.row),
        col2=max(ref.start.col, end.col),
    )


def parse_range_address(address: str) -> CellRange | None:
    """Parse a ``table:cell-range-address`` such as ``$Sheet1.$B$2:$Sheet1.$C$9``.

    Same grammar as a bracketed reference, minus the brackets.
    """
    if not address:
        return None
    ref = parse_reference(address)
    if ref.start is None or ref.start.sheet is None:
        return None
    return resolve(ref, ref.start.sheet)


def format_part_r1c1(part: RefPart, base_row: int, base_col: int) -> str:
    """Render one corner in R1C1 form relative to ``(base_row, base_col)``."""
    out = ""
    if part.sheet:
        out += f"{part.sheet.casefold()}!"
    if part.row is not None:
        out += f"R{part.row + 1}" if part.row_abs else f"R[{part.row - base_row}]"
    if part.col is not None:
        out += f"C{part.col + 1}" if part.col_abs else f"C[{part.col - base_col}]"
    return out


def format_a1(part: RefPart) -> str:
    """Render one corner in user-facing A1 form (no ``$`` markers)."""
    col = col_letters(part.col) if part.col is not None else ""
    row = str(part.row + 1) if part.row is not None else ""
    prefix = f"{part.sheet}." if part.sheet else ""
    return f"{prefix}{col}{row}"


def _quote_sheet(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def format_ref_part(part: RefPart, *, odf: bool = True) -> str:
    """Render one corner back into source form, ``$`` markers and all.

    The inverse of :func:`parse_ref_part`, exact enough to round-trip: column
    letters come back uppercase, which is what LibreOffice writes anyway.

    ``odf=True`` gives the form that goes inside ``[...]`` in a stored formula,
    where a same-sheet reference is still dot-qualified (``.A1``). ``odf=False``
    gives Calc's own A1 convention, where it is not (``A1``) — that is the
    spelling ``XCell.setFormula`` expects, and the two differ *only* here.
    """
    out = ""
    if part.sheet is not None:
        if part.sheet_abs:
            out += "$"
        out += _quote_sheet(part.sheet) if part.sheet_quoted else part.sheet
        out += "."
    elif odf:
        out += "."
    if part.col is not None:
        if part.col_abs:
            out += "$"
        out += col_letters(part.col)
    if part.row is not None:
        if part.row_abs:
            out += "$"
        out += str(part.row + 1)
    return out


def format_reference(ref: Reference, *, odf: bool = True) -> str:
    """The text of a reference, with or without ODF's dot qualification.

    Only defined for references that :func:`resolve` can reason about — an
    external or dead reference has source syntax this does not reproduce, and
    rewriting one is never what a caller wants.
    """
    if ref.invalid or ref.external is not None or ref.start is None:
        raise ValueError(f"cannot render an external or invalid reference: {ref.raw!r}")
    out = format_ref_part(ref.start, odf=odf)
    if ref.end is not None:
        out += ":" + format_ref_part(ref.end, odf=odf)
    return out
