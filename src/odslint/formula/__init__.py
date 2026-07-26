"""OpenFormula (ODF 1.2 part 2) tokenizing and reference handling.

Formulas in ODF are *not* Excel A1. They arrive as ``of:=SUM([.A1:.A5])``:
references are bracketed and dot-qualified, and arguments are separated by ``;``.
Rules must never regex over raw formula text — string literals and sheet names
will bite. Go through :func:`lex` instead.
"""

from __future__ import annotations

from odslint.formula.lexer import CallContext, Token, call_contexts, lex, strip_prefix
from odslint.formula.normalize import normalize_r1c1
from odslint.formula.reference import Reference, RefPart, parse_range_address, resolve

__all__ = [
    "CallContext",
    "Reference",
    "RefPart",
    "Token",
    "call_contexts",
    "lex",
    "normalize_r1c1",
    "parse_range_address",
    "resolve",
    "strip_prefix",
]
