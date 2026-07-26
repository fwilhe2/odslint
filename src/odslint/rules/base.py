"""Rule base class and registry.

Rules are pure: they read the model and yield diagnostics, never mutate. Any
future autofix layer belongs downstream of the diagnostics, not inside a rule.

Severity on a yielded diagnostic is a placeholder — the engine overwrites it
with the configured severity, so rules should not think about configuration.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

from odslint.diagnostics import Diagnostic, Severity
from odslint.model import Cell, Document, Sheet


class Rule:
    #: ``category/kebab-name``. Categories: formula, naming, data, structure,
    #: portability, perf, meta.
    id: ClassVar[str] = ""
    description: ClassVar[str] = ""
    default_severity: ClassVar[Severity] = Severity.WARNING
    #: Option name -> default. Anything a user may reasonably want to tune.
    default_options: ClassVar[dict[str, Any]] = {}

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        merged = dict(self.default_options)
        merged.update(options or {})
        self.options = merged

    def check(self, doc: Document) -> Iterator[Diagnostic]:
        raise NotImplementedError

    # -- helpers -----------------------------------------------------------

    def diag(
        self,
        sheet: Sheet | str,
        cell: Cell | None,
        message: str,
        hint: str | None = None,
    ) -> Diagnostic:
        return Diagnostic(
            rule_id=self.id,
            sheet=sheet if isinstance(sheet, str) else sheet.name,
            row=cell.row if cell is not None else None,
            col=cell.col if cell is not None else None,
            message=message,
            hint=hint,
            severity=self.default_severity,
        )

    def option(self, name: str) -> Any:
        return self.options[name]


REGISTRY: dict[str, type[Rule]] = {}


def register(cls: type[Rule]) -> type[Rule]:
    if not cls.id:
        raise ValueError(f"{cls.__name__} has no rule id")
    if cls.id in REGISTRY:
        raise ValueError(f"duplicate rule id: {cls.id}")
    REGISTRY[cls.id] = cls
    return cls


def all_rules() -> list[type[Rule]]:
    return [REGISTRY[key] for key in sorted(REGISTRY)]
