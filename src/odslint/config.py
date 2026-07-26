"""Configuration: ``.odslintrc.toml``, discovered upward from the linted file.

::

    [odslint]
    fail-on = "warning"       # error | warning | info | never

    [rules."formula/magic-number"]
    severity = "info"         # or "off" to disable the rule
    allowed = [0, 1, -1, 100]

Rule tables carry ``severity`` plus that rule's own options; unknown option keys
are rejected so a typo fails loudly instead of silently doing nothing.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from odslint.diagnostics import Severity

CONFIG_FILENAME = ".odslintrc.toml"


class ConfigError(Exception):
    """The configuration file is unusable."""


@dataclass
class RuleConfig:
    enabled: bool = True
    severity: Severity | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    rules: dict[str, RuleConfig] = field(default_factory=dict)
    #: Lowest severity that makes the run fail. ``None`` means never fail.
    fail_on: Severity | None = Severity.WARNING
    source: Path | None = None

    def for_rule(self, rule_id: str) -> RuleConfig:
        return self.rules.get(rule_id, RuleConfig())

    @classmethod
    def load(cls, path: Path) -> Config:
        from odslint.rules import REGISTRY

        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"{path}: {exc}") from exc

        config = cls(source=path)
        section = data.get("odslint", {})
        if "fail-on" in section:
            config.fail_on = _parse_fail_on(path, section["fail-on"])

        for rule_id, table in data.get("rules", {}).items():
            if rule_id not in REGISTRY:
                raise ConfigError(f"{path}: unknown rule {rule_id!r}")
            if not isinstance(table, dict):
                raise ConfigError(f'{path}: [rules."{rule_id}"] must be a table')

            rule_cls = REGISTRY[rule_id]
            options = {k: v for k, v in table.items() if k != "severity"}
            unknown = set(options) - set(rule_cls.default_options)
            if unknown:
                known = ", ".join(sorted(rule_cls.default_options)) or "(none)"
                raise ConfigError(
                    f"{path}: {rule_id} has no option {sorted(unknown)[0]!r} "
                    f"(known options: {known})"
                )

            enabled = True
            severity: Severity | None = None
            raw_severity = table.get("severity")
            if raw_severity is not None:
                if str(raw_severity).lower() == "off":
                    enabled = False
                else:
                    severity = _parse_severity(path, rule_id, raw_severity)
            config.rules[rule_id] = RuleConfig(enabled, severity, options)

        return config

    @classmethod
    def discover(cls, start: Path) -> Config:
        """Nearest ``.odslintrc.toml`` at or above ``start``; defaults if none."""
        base = start if start.is_dir() else start.parent
        for directory in [base, *base.parents]:
            candidate = directory / CONFIG_FILENAME
            if candidate.is_file():
                return cls.load(candidate)
        return cls()


def _parse_severity(path: Path, rule_id: str, raw: object) -> Severity:
    try:
        return Severity(str(raw).lower())
    except ValueError:
        valid = ", ".join(s.value for s in Severity)
        raise ConfigError(
            f"{path}: {rule_id} severity {raw!r} is not one of: {valid}, off"
        ) from None


def _parse_fail_on(path: Path, raw: object) -> Severity | None:
    text = str(raw).lower()
    if text == "never":
        return None
    try:
        return Severity(text)
    except ValueError:
        valid = ", ".join(s.value for s in Severity)
        raise ConfigError(f"{path}: fail-on {raw!r} is not one of: {valid}, never") from None
