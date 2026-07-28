"""Diagnostics, severities, and the fixes a diagnostic may carry.

A :class:`Fix` is a *description* of an edit, never an edit itself. That is what
lets the same fix be applied two very different ways: :mod:`odslint.fixer`
rewrites the XML of a file on disk, while the LibreOffice extension replays the
same :class:`Edit` objects through UNO against a document open in Calc. Neither
knows about the other, and rules stay pure — they describe, they do not mutate.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


class Applicability(enum.Enum):
    """How much trust an automated fix deserves.

    ``SAFE`` fixes are applied by ``--fix``; ``UNSAFE`` ones need
    ``--unsafe-fixes`` on top. The line is whether the recalculated result of the
    document can change: swapping a reference for a named expression covering
    exactly that range cannot, picking the majority formula for an outlier can.
    """

    SAFE = "safe"
    UNSAFE = "unsafe"


#: An ``Edit`` replaces a cell's formula with ``formula`` (ODF/OpenFormula text,
#: leading ``=``, no ``of:`` prefix).
EDIT_FORMULA = "formula"
#: An ``Edit`` retypes a cell as a number, with ``value`` the ``office:value``
#: payload and ``text`` the display text.
EDIT_NUMBER = "number"


@dataclass(frozen=True)
class Edit:
    """One cell-level change, in coordinates any backend can apply.

    ``row``/``col`` are 0-based, matching the model and :class:`Diagnostic`.
    """

    sheet: str
    row: int
    col: int
    kind: str
    formula: str | None = None
    #: The same formula in Calc's A1 convention (``=SUM(A4:C4)``), for backends
    #: that go through ``XCell.setFormula`` rather than writing XML. Handing UNO
    #: the stored ``[.A4:.C4]`` form does not fail — it silently stores a broken
    #: formula — so the two spellings travel together.
    formula_a1: str | None = None
    value: str | None = None
    text: str | None = None

    @property
    def target(self) -> tuple[str, int, int]:
        return (self.sheet, self.row, self.col)


@dataclass(frozen=True)
class Fix:
    """A mechanical edit that resolves a diagnostic."""

    title: str
    applicability: Applicability
    edits: tuple[Edit, ...] = field(default_factory=tuple)

    @property
    def is_safe(self) -> bool:
        return self.applicability is Applicability.SAFE


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
    fix: Fix | None = None

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
