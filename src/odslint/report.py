"""Diagnostic output formats."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence

from odslint.diagnostics import Diagnostic, Edit, Fix, Severity

FORMATS = ("text", "json")

#: Marker on a diagnostic that carries a fix, borrowed from ruff.
FIXABLE_MARKER = "[*]"


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
        marker = f" {FIXABLE_MARKER}" if diagnostic.fix is not None else ""
        lines.append(
            f"{path}:{diagnostic.location}: {severity} "
            f"[{diagnostic.rule_id}]{marker} {diagnostic.message}"
        )
        if diagnostic.hint:
            hint = f"hint: {diagnostic.hint}"
            lines.append(f"    {_DIM}{hint}{_RESET}" if color else f"    {hint}")

    lines.append(summary(diagnostics))
    fixable = fixable_summary(diagnostics)
    if fixable:
        lines.append(fixable)
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


def fixable_summary(diagnostics: Sequence[Diagnostic]) -> str | None:
    """The ruff-style ``N fixable with --fix`` line, or ``None`` if nothing is."""
    safe = sum(1 for d in diagnostics if d.fix is not None and d.fix.is_safe)
    unsafe = sum(1 for d in diagnostics if d.fix is not None and not d.fix.is_safe)
    if safe and unsafe:
        return f"{safe} fixable with --fix ({unsafe} more with --unsafe-fixes)"
    if safe:
        return f"{safe} fixable with --fix"
    if unsafe:
        return f"{unsafe} fixable with --unsafe-fixes"
    return None


def _edit_json(edit: Edit) -> dict[str, object]:
    return {
        "sheet": edit.sheet,
        "row": edit.row,
        "column": edit.col,
        "kind": edit.kind,
        "formula": edit.formula,
        "formula_a1": edit.formula_a1,
        "value": edit.value,
        "text": edit.text,
    }


def _fix_json(fix: Fix | None) -> dict[str, object] | None:
    if fix is None:
        return None
    return {
        "title": fix.title,
        "applicability": fix.applicability.value,
        "edits": [_edit_json(e) for e in fix.edits],
    }


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
            "fix": _fix_json(d.fix),
        }
        for d in diagnostics
    ]
    return json.dumps(payload, indent=2, ensure_ascii=False)
