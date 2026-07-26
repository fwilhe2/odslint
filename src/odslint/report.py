"""Diagnostic output formats."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence

from odslint.diagnostics import Diagnostic, Severity

FORMATS = ("text", "json")


def render(diagnostics: Sequence[Diagnostic], fmt: str, color: bool = False) -> str:
    if fmt == "json":
        return render_json(diagnostics)
    if fmt == "text":
        return render_text(diagnostics, color=color)
    raise ValueError(f"unknown format: {fmt}")


_COLORS = {
    Severity.ERROR: "\033[31m",
    Severity.WARNING: "\033[33m",
    Severity.INFO: "\033[36m",
}
_RESET = "\033[0m"
_DIM = "\033[2m"


def render_text(diagnostics: Sequence[Diagnostic], color: bool = False) -> str:
    lines: list[str] = []
    for diagnostic in diagnostics:
        path = str(diagnostic.path) if diagnostic.path else "<document>"
        severity = str(diagnostic.severity)
        if color:
            severity = f"{_COLORS[diagnostic.severity]}{severity}{_RESET}"
        lines.append(
            f"{path}:{diagnostic.location}: {severity} [{diagnostic.rule_id}] {diagnostic.message}"
        )
        if diagnostic.hint:
            hint = f"hint: {diagnostic.hint}"
            lines.append(f"    {_DIM}{hint}{_RESET}" if color else f"    {hint}")

    lines.append(summary(diagnostics))
    return "\n".join(lines)


def summary(diagnostics: Sequence[Diagnostic]) -> str:
    if not diagnostics:
        return "No problems found."
    counts = Counter(d.severity for d in diagnostics)
    parts = [
        f"{counts[s]} {s.value}{'s' if counts[s] != 1 else ''}"
        for s in (Severity.ERROR, Severity.WARNING, Severity.INFO)
        if counts[s]
    ]
    total = len(diagnostics)
    return f"{total} problem{'s' if total != 1 else ''} ({', '.join(parts)})"


def render_json(diagnostics: Sequence[Diagnostic]) -> str:
    payload = [
        {
            "path": str(d.path) if d.path else None,
            "rule": d.rule_id,
            "severity": d.severity.value,
            "sheet": d.sheet,
            "cell": d.location.split("!", 1)[1] if "!" in d.location else None,
            "row": d.row,
            "column": d.col,
            "message": d.message,
            "hint": d.hint,
        }
        for d in diagnostics
    ]
    return json.dumps(payload, indent=2, ensure_ascii=False)
