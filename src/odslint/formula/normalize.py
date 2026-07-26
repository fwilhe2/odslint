"""R1C1 normalization — the fingerprint used to compare sibling formulas.

Two cells that were filled from the same source produce the same fingerprint no
matter where they sit; a cell someone hand-edited does not. That is the whole
basis of ``formula/inconsistent-in-range``.
"""

from __future__ import annotations

from odslint.formula.lexer import lex
from odslint.formula.reference import Reference, format_part_r1c1


def reference_r1c1(ref: Reference, row: int, col: int) -> str:
    if ref.invalid:
        return "#REF!"
    prefix = f"'{ref.external}'#" if ref.external else ""
    if ref.start is None:
        return f"{prefix}?"
    out = prefix + format_part_r1c1(ref.start, row, col)
    if ref.end is not None:
        out += ":" + format_part_r1c1(ref.end, row, col)
    return out


def normalize_r1c1(formula: str, row: int, col: int) -> str:
    """Position-independent fingerprint of ``formula`` as written in ``(row, col)``.

    Whitespace is dropped and function/name casing folded, so cosmetic edits do
    not read as structural differences.
    """
    parts: list[str] = []
    for token in lex(formula):
        if token.kind == "ws":
            continue
        if token.kind == "ref" and token.ref is not None:
            parts.append(reference_r1c1(token.ref, row, col))
        elif token.kind in ("func", "name"):
            parts.append(token.text.upper())
        elif token.kind == "number":
            number = token.number
            parts.append(repr(number) if number is not None else token.text)
        else:
            parts.append(token.text)
    return "".join(parts)
