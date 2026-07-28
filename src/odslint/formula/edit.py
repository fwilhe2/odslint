"""Rewriting formula text without reparsing it.

Every edit here is a splice against the token stream, never a regex over the
formula — :func:`~odslint.formula.lexer.lex` is quote-aware and the naive
approaches are not. Tokens carry :attr:`~odslint.formula.lexer.Token.pos`, so a
replacement is an exact span and everything the rule did not touch, including
whitespace and the author's capitalization, survives untouched.

Offsets are *body* offsets: ``lex`` strips the ``of:`` prefix and the leading
``=`` before it starts counting, so :func:`split_body` is how you get back to
the original string.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from odslint.diagnostics import EDIT_FORMULA, Edit
from odslint.formula.lexer import lex, strip_prefix
from odslint.formula.reference import Reference, RefPart, format_reference

#: ``(pos, length, replacement)`` against the formula body.
Span = tuple[int, int, str]


def split_body(formula: str) -> tuple[str, str]:
    """``of:=SUM([.A1])`` -> ``("of:=", "SUM([.A1])")``.

    The second element is the string that lexer positions index into.
    """
    stripped = strip_prefix(formula)
    prefix = formula[: len(formula) - len(stripped)]
    if stripped.startswith("="):
        return prefix + "=", stripped[1:]
    return prefix, stripped


def splice(formula: str, spans: Sequence[Span]) -> str:
    """Apply replacement spans to a formula, keeping its prefix and ``=``.

    Spans are applied right-to-left so earlier positions stay valid. Overlapping
    spans are a caller bug and raise.
    """
    prefix, body = split_body(formula)
    ordered = sorted(spans, key=lambda span: span[0], reverse=True)

    previous_start = len(body)
    for pos, length, replacement in ordered:
        if pos < 0 or pos + length > len(body):
            raise ValueError(f"span ({pos}, {length}) is outside the formula body {body!r}")
        if pos + length > previous_start:
            raise ValueError(f"overlapping spans in {formula!r}")
        body = body[:pos] + replacement + body[pos + length :]
        previous_start = pos

    return prefix + body


def replace_token_text(formula: str, text: str, replacement: str, kind: str = "ref") -> str:
    """Replace *every* token of ``kind`` whose source text is exactly ``text``.

    Rules deduplicate their diagnostics by token text — one finding per distinct
    reference rather than one per occurrence — so the matching fix has to cover
    all the occurrences that one diagnostic stood for.
    """
    spans = [
        (t.pos, len(t.text), replacement) for t in lex(formula) if t.kind == kind and t.text == text
    ]
    if not spans:
        return formula
    return splice(formula, spans)


# -- moving a formula to another cell ---------------------------------------


def _shift_part(part: RefPart, d_row: int, d_col: int) -> RefPart | None:
    """Move a corner by a cell offset. ``None`` if it would leave the sheet.

    ``$``-anchored components do not move — that is the entire meaning of the
    marker, and it is why a translated formula is not just a text copy.
    """
    row = part.row if part.row is None or part.row_abs else part.row + d_row
    col = part.col if part.col is None or part.col_abs else part.col + d_col
    if (row is not None and row < 0) or (col is not None and col < 0):
        return None
    return dataclasses.replace(part, row=row, col=col)


def _shift_reference(ref: Reference, d_row: int, d_col: int) -> str | None:
    if ref.invalid or ref.external is not None or ref.start is None:
        return None
    start = _shift_part(ref.start, d_row, d_col)
    if start is None:
        return None
    end = None
    if ref.end is not None:
        end = _shift_part(ref.end, d_row, d_col)
        if end is None:
            return None
    return format_reference(dataclasses.replace(ref, start=start, end=end))


def translate(formula: str, from_row: int, from_col: int, to_row: int, to_col: int) -> str | None:
    """Rewrite a formula as if it had been filled from one cell to another.

    Relative references move with the cell, absolute ones stay put — exactly
    what Calc does on copy. Returns ``None`` when the result would not be
    trustworthy: an external or dead reference, whose source syntax this cannot
    reproduce, or a relative reference that would land off the top or left edge
    of the sheet. Callers should read that as "offer no fix here".
    """
    d_row = to_row - from_row
    d_col = to_col - from_col

    spans: list[Span] = []
    for token in lex(formula):
        if token.kind != "ref":
            continue
        if token.ref is None:
            return None
        shifted = _shift_reference(token.ref, d_row, d_col)
        if shifted is None:
            return None
        spans.append((token.pos, len(token.text), f"[{shifted}]"))

    return splice(formula, spans)


# -- the two spellings of a formula -----------------------------------------


def to_a1(formula: str) -> str | None:
    """Rewrite a stored formula in Calc's own A1 convention.

    ``XCell.setFormula`` wants ``=SUM(A4:C4)``, not the ``=SUM([.A4:.C4])`` that
    lives in ``table:formula``. Handing it the stored form does not fail — it
    quietly stores a broken formula that evaluates to 0 — so the conversion has
    to happen before a fix ever reaches UNO.

    Only the references differ between the two spellings: separators, operators,
    function names, string literals and named expressions are identical, which
    is why this is a splice rather than a re-render. ``None`` when the formula
    holds an external or dead reference that cannot be spelled either way.
    """
    spans: list[Span] = []
    for token in lex(formula):
        if token.kind != "ref":
            continue
        if token.ref is None:
            return None
        try:
            spans.append((token.pos, len(token.text), format_reference(token.ref, odf=False)))
        except ValueError:
            return None

    return splice(formula, spans)


def formula_edit(sheet: str, row: int, col: int, formula: str) -> Edit:
    """A formula :class:`Edit` carrying both spellings of the same change."""
    return Edit(
        sheet=sheet,
        row=row,
        col=col,
        kind=EDIT_FORMULA,
        formula=formula,
        formula_a1=to_a1(formula),
    )
