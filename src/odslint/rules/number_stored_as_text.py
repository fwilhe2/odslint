"""data/number-stored-as-text — values that look numeric but are strings.

These silently drop out of SUM, sort in the wrong order, and break lookups
against genuinely numeric keys. Usually the result of a CSV import or a paste.

The rule has to stay conservative: plenty of strings *look* numeric but are
identifiers. Leading zeros (``00742``), long digit strings, and anything with
internal spacing are treated as codes, not numbers.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any, ClassVar

from odslint.diagnostics import Diagnostic, Severity
from odslint.model import Document
from odslint.rules.base import Rule, register

_CURRENCY = "€$£¥₣₤"
#: Space characters used as a thousands separator (fr/ru/SI conventions).
_GROUPING_SPACES = "\u00a0\u2009\u202f"
_SPACE_GROUPED_RE = re.compile(r"^[+-]?\d{1,3}(?: \d{3})+(?:[.,]\d+)?$")

# 1.234,56 / 1,234.56 / 1234.56 / -12
_GROUPED_RE = re.compile(r"^[+-]?\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?$")
_PLAIN_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LOCAL_DATE_RE = re.compile(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}$")


def classify(text: str) -> str | None:
    """``"number"``, ``"date"``, or ``None`` if the text is genuinely text."""
    value = text.strip()
    if not value or len(value) > 40:
        return None

    if _ISO_DATE_RE.match(value) or _LOCAL_DATE_RE.match(value):
        return "date"

    stripped = value.rstrip("%").strip().replace("CHF", "")
    for symbol in _CURRENCY:
        stripped = stripped.replace(symbol, "")
    for space in _GROUPING_SPACES:
        stripped = stripped.replace(space, " ")
    stripped = stripped.strip()
    if not stripped:
        return None

    if _SPACE_GROUPED_RE.match(stripped):
        return "number"
    # Any space left over is not grouping -- "12 34" is a label, not a quantity.
    if any(ch.isspace() for ch in stripped):
        return None

    digits = stripped.lstrip("+-")
    # Codes, not quantities: leading zeros are load-bearing, and nobody types a
    # 16-digit number they intend to do arithmetic on.
    if digits.startswith("0") and not digits.startswith("0.") and not digits.startswith("0,"):
        return None
    if len(digits.replace(".", "").replace(",", "")) > 15:
        return None

    if _GROUPED_RE.match(stripped) or _PLAIN_RE.match(stripped):
        return "number"
    return None


@register
class NumberStoredAsText(Rule):
    id: ClassVar[str] = "data/number-stored-as-text"
    description: ClassVar[str] = "String cells whose content parses as a number or date"
    default_severity: ClassVar[Severity] = Severity.ERROR
    default_options: ClassVar[dict[str, Any]] = {
        # Set false to accept text-typed dates (some exports never type them).
        "check_dates": True,
    }

    def check(self, doc: Document) -> Iterator[Diagnostic]:
        check_dates: bool = self.option("check_dates")

        for sheet in doc.sheets:
            for cell in sheet.iter_cells():
                if cell.is_formula or cell.value_type != "string":
                    continue
                kind = classify(cell.text)
                if kind is None or (kind == "date" and not check_dates):
                    continue
                yield self.diag(
                    sheet,
                    cell,
                    f"{cell.text.strip()!r} is stored as text but reads as a {kind}",
                    hint="it will be skipped by SUM and sort as text; re-enter it or "
                    "use Data > Text to Columns to convert the column",
                )
