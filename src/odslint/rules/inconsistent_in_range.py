"""formula/inconsistent-in-range — the copy-paste error detector.

Within a contiguous run of formula cells down a column or across a row, every
cell normally shares one R1C1 shape. When one cell deviates, it is almost always
either a hand-patched value or a fill that stopped short. This is the single
highest-value rule in a spreadsheet linter, and it is why the model keeps exact
cell positions rather than just values.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import Any, ClassVar

from odslint.diagnostics import Diagnostic, Severity
from odslint.formula.normalize import normalize_r1c1
from odslint.model import Cell, Document, Sheet, a1
from odslint.rules.base import Rule, register


@register
class InconsistentInRange(Rule):
    id: ClassVar[str] = "formula/inconsistent-in-range"
    description: ClassVar[str] = (
        "A formula that breaks the pattern of the contiguous block around it"
    )
    default_severity: ClassVar[Severity] = Severity.ERROR
    default_options: ClassVar[dict[str, Any]] = {
        # Shortest run worth judging. Two cells have no majority.
        "min_run": 3,
        # Share of the run that must agree before the rest count as deviations.
        "majority_ratio": 0.6,
    }

    def check(self, doc: Document) -> Iterator[Diagnostic]:
        min_run: int = self.option("min_run")
        ratio: float = self.option("majority_ratio")

        for sheet in doc.sheets:
            # A cell can sit in both a column run and a row run; report it once,
            # preferring whichever run found it first (columns, by convention).
            reported: dict[tuple[int, int], Diagnostic] = {}
            for run in _runs(sheet, min_run):
                for diagnostic in self._check_run(sheet, run, ratio):
                    key = (diagnostic.row or 0, diagnostic.col or 0)
                    reported.setdefault(key, diagnostic)
            for key in sorted(reported):
                yield reported[key]

    def _check_run(self, sheet: Sheet, run: list[Cell], ratio: float) -> Iterator[Diagnostic]:
        fingerprints = [normalize_r1c1(cell.formula or "", cell.row, cell.col) for cell in run]
        counts = Counter(fingerprints)
        majority, majority_count = counts.most_common(1)[0]
        if majority_count == len(run):
            return
        if majority_count / len(run) < ratio:
            # No dominant pattern: this is a heterogeneous block, not a mistake.
            return

        span = f"{a1(run[0].row, run[0].col)}:{a1(run[-1].row, run[-1].col)}"
        for cell, fingerprint in zip(run, fingerprints, strict=True):
            if fingerprint == majority:
                continue
            example = next(c for c, f in zip(run, fingerprints, strict=True) if f == majority)
            yield self.diag(
                sheet,
                cell,
                f"formula {cell.formula!r} breaks the pattern of {span} "
                f"({majority_count} of {len(run)} cells share one shape)",
                hint=f"the block otherwise reads like {example.formula!r} in "
                f"{a1(example.row, example.col)}",
            )


def _runs(sheet: Sheet, min_run: int) -> Iterator[list[Cell]]:
    """Maximal contiguous runs of formula cells, down columns then across rows."""
    cells = {(c.row, c.col): c for c in sheet.formula_cells()}
    if not cells:
        return

    by_col: dict[int, list[int]] = {}
    by_row: dict[int, list[int]] = {}
    for row, col in cells:
        by_col.setdefault(col, []).append(row)
        by_row.setdefault(row, []).append(col)

    for col in sorted(by_col):
        for group in _consecutive(sorted(by_col[col])):
            if len(group) >= min_run:
                yield [cells[(row, col)] for row in group]

    for row in sorted(by_row):
        for group in _consecutive(sorted(by_row[row])):
            if len(group) >= min_run:
                yield [cells[(row, col)] for col in group]


def _consecutive(values: list[int]) -> Iterator[list[int]]:
    group: list[int] = []
    for value in values:
        if group and value != group[-1] + 1:
            yield group
            group = []
        group.append(value)
    if group:
        yield group
