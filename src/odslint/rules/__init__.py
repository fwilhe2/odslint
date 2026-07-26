"""Rule modules. Importing this package registers every rule."""

from __future__ import annotations

from odslint.rules import (  # noqa: F401  (imported for registration side effect)
    inconsistent_in_range,
    magic_number,
    number_stored_as_text,
    prefer_named_range,
)
from odslint.rules.base import REGISTRY, Rule, all_rules, register

__all__ = ["REGISTRY", "Rule", "all_rules", "register"]
