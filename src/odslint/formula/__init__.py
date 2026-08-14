"""OpenFormula (ODF 1.2 part 2) tokenizing and reference handling.

Formulas in ODF are *not* Excel A1. They arrive as ``of:=SUM([.A1:.A5])``:
references are bracketed and dot-qualified, and arguments are separated by ``;``.
Rules must never regex over raw formula text — string literals and sheet names
will bite. Go through :func:`odslint.formula.lexer.lex` instead.

Import from the submodules (:mod:`~odslint.formula.lexer`,
:mod:`~odslint.formula.reference`, :mod:`~odslint.formula.normalize`,
:mod:`~odslint.formula.edit`) rather than from this package.
"""

from __future__ import annotations
