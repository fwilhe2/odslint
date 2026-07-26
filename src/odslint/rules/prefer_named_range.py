"""formula/prefer-named-range — the motivating rule.

``=B7*C7`` says nothing; ``=Hours*Rate`` says everything. Two situations are
worth flagging, and both are deliberately narrow — a reference to the cell one
row up inside a fill block is *not* a naming failure, and flagging it would make
the rule unusable on real sheets.

1. A reference whose target exactly matches an existing named expression. The
   name is right there and the author typed the address anyway.
2. An absolute (``$B$1``) or cross-sheet reference to a literal constant. That
   is a parameter of the model, and parameters deserve names. Where a text
   label sits next to the constant, it becomes the suggested name.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

from odslint.diagnostics import Diagnostic, Severity
from odslint.formula.lexer import lex
from odslint.formula.reference import resolve
from odslint.model import NUMERIC_TYPES, CellRange, Document, Sheet, a1
from odslint.rules.base import Rule, register


def suggest_name(label: str) -> str | None:
    """Turn a label like ``Tax rate (%)`` into a usable name like ``Tax_rate``."""
    cleaned: list[str] = []
    for ch in label.strip():
        if ch.isalnum() or ch == "_":
            cleaned.append(ch)
        elif cleaned and cleaned[-1] != "_":
            cleaned.append("_")
    name = "".join(cleaned).strip("_")
    if not name:
        return None
    if name[0].isdigit():
        name = f"_{name}"
    return name


@register
class PreferNamedRange(Rule):
    id: ClassVar[str] = "formula/prefer-named-range"
    description: ClassVar[str] = (
        "Formulas should reference named expressions instead of bare cell addresses"
    )
    default_severity: ClassVar[Severity] = Severity.WARNING
    default_options: ClassVar[dict[str, Any]] = {
        # Flag absolute / cross-sheet references to constants that have no name yet.
        "flag_unnamed_constants": True,
    }

    def check(self, doc: Document) -> Iterator[Diagnostic]:
        for sheet in doc.sheets:
            names = [n for n in doc.names_visible_from(sheet) if n.target is not None]
            by_range: dict[CellRange, str] = {}
            for named in names:
                assert named.target is not None
                by_range.setdefault(named.target, named.name)

            for cell in sheet.formula_cells():
                assert cell.formula is not None
                seen: set[str] = set()
                for token in lex(cell.formula):
                    if token.kind != "ref" or token.ref is None:
                        continue
                    target = resolve(token.ref, sheet.name)
                    if target is None or token.text in seen:
                        continue
                    seen.add(token.text)

                    existing = by_range.get(target)
                    if existing is not None:
                        yield self.diag(
                            sheet,
                            cell,
                            f"references {target} directly, but the named expression "
                            f"{existing!r} already covers exactly that range",
                            hint=f"replace {token.text} with {existing}",
                        )
                        continue

                    if self.option("flag_unnamed_constants"):
                        diagnostic = self._check_constant(doc, sheet, cell, token, target)
                        if diagnostic is not None:
                            yield diagnostic

    def _check_constant(
        self,
        doc: Document,
        sheet: Sheet,
        cell: Any,
        token: Any,
        target: CellRange,
    ) -> Diagnostic | None:
        ref = token.ref
        cross_sheet = ref.sheet is not None and ref.sheet.casefold() != sheet.name.casefold()
        if not (ref.is_single_cell and (ref.is_absolute or cross_sheet)):
            return None

        target_sheet = doc.sheet(target.sheet)
        if target_sheet is None:
            return None
        constant = target_sheet.cell(target.row1, target.col1)
        if constant is None or constant.is_formula or constant.value_type not in NUMERIC_TYPES:
            return None

        label = _adjacent_label(target_sheet, target.row1, target.col1)
        suggestion = suggest_name(label) if label else None
        where = f"{a1(target.row1, target.col1)}"
        if cross_sheet:
            where = f"{target.sheet}.{where}"
        message = f"{where} is a constant input referenced by address"
        hint = (
            f"define a named expression (e.g. {suggestion!r}, from the label {label!r}) "
            f"and use it here"
            if suggestion
            else "give the cell a name under Sheet > Named Ranges and use it here"
        )
        return self.diag(sheet, cell, message, hint)


def _adjacent_label(sheet: Sheet, row: int, col: int) -> str | None:
    """Text label immediately left of, or above, a constant cell."""
    for neighbour in ((row, col - 1), (row - 1, col)):
        if neighbour[1] < 0 or neighbour[0] < 0:
            continue
        candidate = sheet.cell(*neighbour)
        if (
            candidate is not None
            and not candidate.is_formula
            and candidate.value_type == "string"
            and candidate.text.strip()
        ):
            return candidate.text.strip()
    return None
