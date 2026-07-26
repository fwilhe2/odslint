"""The lint pipeline: load -> model -> rules -> suppression -> sorted diagnostics."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from pathlib import Path

from odslint.config import Config
from odslint.diagnostics import Diagnostic, Severity
from odslint.loader import load
from odslint.model import Document
from odslint.rules import REGISTRY, Rule
from odslint.suppress import build_index


def select_rules(config: Config, only: Iterable[str] | None = None) -> list[Rule]:
    """Instantiate the enabled rules, honouring an explicit ``--rule`` selection."""
    selected = set(only) if only else None
    rules: list[Rule] = []
    for rule_id in sorted(REGISTRY):
        if selected is not None and rule_id not in selected:
            continue
        rule_config = config.for_rule(rule_id)
        if selected is None and not rule_config.enabled:
            continue
        rules.append(REGISTRY[rule_id](rule_config.options))
    return rules


def lint_document(doc: Document, config: Config, rules: list[Rule]) -> list[Diagnostic]:
    suppressions = build_index(doc)
    out: list[Diagnostic] = []

    for rule in rules:
        severity = config.for_rule(rule.id).severity or rule.default_severity
        for diagnostic in rule.check(doc):
            diagnostic = dataclasses.replace(diagnostic, severity=severity, path=doc.path)
            if suppressions.suppresses(diagnostic):
                continue
            out.append(diagnostic)

    out.sort(key=lambda d: d.sort_key)
    return out


def lint_file(
    path: str | Path, config: Config, rules: list[Rule] | None = None
) -> list[Diagnostic]:
    doc = load(path)
    return lint_document(doc, config, rules if rules is not None else select_rules(config))


def worst_severity(diagnostics: Iterable[Diagnostic]) -> Severity | None:
    severities = [d.severity for d in diagnostics]
    return max(severities, key=lambda s: s.rank) if severities else None


def should_fail(diagnostics: Iterable[Diagnostic], config: Config) -> bool:
    if config.fail_on is None:
        return False
    worst = worst_severity(diagnostics)
    return worst is not None and worst.rank >= config.fail_on.rank
