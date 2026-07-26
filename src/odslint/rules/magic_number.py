"""formula/magic-number — literals buried in formulas.

``=B7*1.19`` hides a tax rate where nobody can find or change it. The rate
belongs in a labelled cell with a name, so that changing it is a one-cell edit
and the sheet documents itself.

Not every literal is magic: ``ROUND(x; 2)`` and ``VLOOKUP(k; range; 3; 0)``
carry structural arguments that would be *less* readable as named cells, so
those positions are excluded by default.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

from odslint.diagnostics import Diagnostic, Severity
from odslint.formula.lexer import CallContext, Token, call_contexts, lex
from odslint.model import Document
from odslint.rules.base import Rule, register

#: ``function -> argument indices (0-based)`` whose numbers are structural.
STRUCTURAL_ARGS: dict[str, set[int]] = {
    "VLOOKUP": {2, 3},
    "HLOOKUP": {2, 3},
    "LOOKUP": {2},
    "INDEX": {1, 2},
    "MATCH": {2},
    "OFFSET": {1, 2, 3, 4},
    "ROUND": {1},
    "ROUNDUP": {1},
    "ROUNDDOWN": {1},
    "TRUNC": {1},
    "LARGE": {1},
    "SMALL": {1},
    "RANK": {2},
    "WEEKDAY": {1},
    "SUBTOTAL": {0},
    "CHOOSE": {0},
    "DATE": {0, 1, 2},
    "TIME": {0, 1, 2},
    "LEFT": {1},
    "RIGHT": {1},
    "MID": {1, 2},
    "REPT": {1},
    "POWER": {1},
    "COLUMN": {0},
    "ROW": {0},
}


@register
class MagicNumber(Rule):
    id: ClassVar[str] = "formula/magic-number"
    description: ClassVar[str] = (
        "Numeric literals in formulas should live in labelled, named input cells"
    )
    default_severity: ClassVar[Severity] = Severity.WARNING
    default_options: ClassVar[dict[str, Any]] = {
        # Values too trivial to be worth naming.
        "allowed": [0.0, 1.0, -1.0],
        # Extra ``"FUNC:index"`` entries to treat as structural.
        "structural_args": [],
    }

    def check(self, doc: Document) -> Iterator[Diagnostic]:
        allowed = {float(v) for v in self.option("allowed")}
        structural = _merge_structural(self.option("structural_args"))

        for sheet in doc.sheets:
            for cell in sheet.formula_cells():
                assert cell.formula is not None
                tokens = lex(cell.formula)
                contexts = call_contexts(tokens)
                reported: set[float] = set()

                for index, token in enumerate(tokens):
                    if token.kind != "number":
                        continue
                    value = _signed_value(tokens, index)
                    if value is None or value in allowed or value in reported:
                        continue
                    if _is_structural(contexts[index], structural):
                        continue
                    reported.add(value)
                    yield self.diag(
                        sheet,
                        cell,
                        f"formula contains the literal {token.text}",
                        hint="move it to a labelled input cell and reference that cell by name",
                    )


def _merge_structural(extra: list[str]) -> dict[str, set[int]]:
    merged = {name: set(args) for name, args in STRUCTURAL_ARGS.items()}
    for entry in extra:
        name, _, index = str(entry).partition(":")
        if not index.isdigit():
            continue
        merged.setdefault(name.upper(), set()).add(int(index))
    return merged


def _is_structural(context: CallContext | None, structural: dict[str, set[int]]) -> bool:
    if context is None or context.name is None:
        return False
    return context.arg_index in structural.get(context.name, set())


def _signed_value(tokens: list[Token], index: int) -> float | None:
    """Numeric value including a unary minus, so ``-1`` reads as ``-1.0``."""
    value = tokens[index].number
    if value is None:
        return None

    minus = _prev_significant(tokens, index)
    if minus is None or tokens[minus].text != "-":
        return value
    before = _prev_significant(tokens, minus)
    is_unary = before is None or tokens[before].kind in ("op", "sep", "lparen", "brace")
    return -value if is_unary else value


def _prev_significant(tokens: list[Token], index: int) -> int | None:
    for candidate in range(index - 1, -1, -1):
        if tokens[candidate].kind != "ws":
            return candidate
    return None
