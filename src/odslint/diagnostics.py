"""Diagnostics and severities."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path


class Severity(enum.Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def rank(self) -> int:
        return _RANK[self]

    def __str__(self) -> str:
        return self.value


_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.ERROR: 2}


@dataclass(frozen=True)
class Diagnostic:
    """One finding, anchored to a cell (or to a sheet when ``row``/``col`` are None)."""

    rule_id: str
    sheet: str
    message: str
    row: int | None = None
    col: int | None = None
    hint: str | None = None
    severity: Severity = Severity.WARNING
    path: Path | None = None

    @property
    def location(self) -> str:
        from odslint.model import a1

        if self.row is None or self.col is None:
            return self.sheet
        return f"{self.sheet}!{a1(self.row, self.col)}"

    @property
    def sort_key(self) -> tuple[str, int, int, str]:
        return (
            self.sheet,
            self.row if self.row is not None else -1,
            self.col if self.col is not None else -1,
            self.rule_id,
        )
