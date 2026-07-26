"""Inline suppression.

A spreadsheet has no comment syntax to hang a ``# noqa`` off, so directives live
in **cell annotations** (Insert > Comment). An annotation anywhere on a cell
containing::

    odslint-disable
    odslint-disable formula/magic-number
    odslint-disable formula/magic-number, data/number-stored-as-text

suppresses all, or the listed, diagnostics for that cell. The rest of the
annotation text is ignored, so a directive can sit alongside a real note.

Rules never look at annotations themselves — suppression is applied centrally in
the engine so that every rule behaves identically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from odslint.diagnostics import Diagnostic
from odslint.model import Document

DIRECTIVE_RE = re.compile(r"odslint-disable(?P<rules>[^\n]*)", re.IGNORECASE)
_RULE_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*/[A-Za-z0-9-]+")


@dataclass
class SuppressionIndex:
    #: ``(sheet, row, col)`` -> rule ids, empty set meaning "all rules".
    entries: dict[tuple[str, int, int], set[str]] = field(default_factory=dict)

    def suppresses(self, diagnostic: Diagnostic) -> bool:
        if diagnostic.row is None or diagnostic.col is None:
            return False
        key = (diagnostic.sheet, diagnostic.row, diagnostic.col)
        rules = self.entries.get(key)
        if rules is None:
            return False
        return not rules or diagnostic.rule_id in rules


def build_index(doc: Document) -> SuppressionIndex:
    index = SuppressionIndex()
    for sheet in doc.sheets:
        for cell in sheet.cells.values():
            for annotation in cell.annotations:
                for match in DIRECTIVE_RE.finditer(annotation):
                    listed = set(_RULE_RE.findall(match.group("rules")))
                    key = (sheet.name, cell.row, cell.col)
                    existing = index.entries.get(key)
                    if existing is None:
                        index.entries[key] = listed
                    elif existing and listed:
                        existing.update(listed)
                    else:
                        # A bare directive wins: it means "all rules here".
                        index.entries[key] = set()
    return index
