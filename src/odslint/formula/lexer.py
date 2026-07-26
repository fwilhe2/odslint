"""OpenFormula tokenizer.

Deliberately not a full parser: the rules we have need reference extraction,
number literals with their enclosing-call context, and a stable token stream to
normalize against. A Pratt parser can be layered on top of these tokens later
without changing the rule API.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from odslint.formula.reference import Reference, parse_reference

#: Token kinds. ``func`` is a name immediately followed by ``(``.
KINDS = frozenset(
    {
        "ws",
        "ref",
        "string",
        "number",
        "name",
        "func",
        "error",
        "op",
        "sep",
        "lparen",
        "rparen",
        "brace",
        "other",
    }
)

_NUMBER_RE = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_NAME_RE = re.compile(r"[A-Za-z_\\][A-Za-z0-9_.\\]*")
_ERROR_RE = re.compile(r"#(?:REF!|DIV/0!|N/A|VALUE!|NAME\?|NUM!|NULL!|ERR:\d+)", re.I)
_TWO_CHAR_OPS = ("<=", ">=", "<>", "!=")
_ONE_CHAR_OPS = "+-*/^&=<>%!~:"


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    pos: int
    ref: Reference | None = None

    @property
    def number(self) -> float | None:
        if self.kind != "number":
            return None
        try:
            return float(self.text)
        except ValueError:
            return None


@dataclass(frozen=True)
class CallContext:
    """The innermost function call surrounding a token, and which argument it is in."""

    name: str | None
    arg_index: int


def strip_prefix(formula: str) -> str:
    """``of:=SUM([.A1])`` -> ``=SUM([.A1])``. Idempotent; tolerates no prefix."""
    eq = formula.find("=")
    return formula[eq:] if eq != -1 else formula


def _match_bracket(text: str, start: int) -> int:
    """Index just past the ``]`` closing the bracket at ``start``, quote-aware."""
    i = start + 1
    in_quote = False
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if in_quote and text[i : i + 2] == "''":
                i += 2
                continue
            in_quote = not in_quote
        elif ch == "]" and not in_quote:
            return i + 1
        i += 1
    return len(text)


def _match_string(text: str, start: int) -> int:
    i = start + 1
    while i < len(text):
        if text[i] == '"':
            if text[i : i + 2] == '""':
                i += 2
                continue
            return i + 1
        i += 1
    return len(text)


def lex(formula: str) -> list[Token]:
    """Tokenize a formula. Accepts it with or without the ``of:`` prefix.

    Never raises: unrecognized input becomes ``other`` tokens so that a rule
    running over a weird formula degrades instead of crashing the whole run.
    """
    text = strip_prefix(formula)
    if text.startswith("="):
        text = text[1:]

    tokens: list[Token] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if ch.isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            tokens.append(Token("ws", text[i:j], i))
            i = j
            continue

        if ch == "[":
            j = _match_bracket(text, i)
            inner = text[i + 1 : j - 1] if text[j - 1 : j] == "]" else text[i + 1 : j]
            tokens.append(Token("ref", text[i:j], i, parse_reference(inner)))
            i = j
            continue

        if ch == '"':
            j = _match_string(text, i)
            tokens.append(Token("string", text[i:j], i))
            i = j
            continue

        if ch == "#":
            match = _ERROR_RE.match(text, i)
            if match:
                tokens.append(Token("error", match.group(0), i))
                i = match.end()
                continue

        if text.startswith("$$", i):
            # Explicit named-expression reference: $$Name or $$'Some Name'
            j = i + 2
            if j < n and text[j] == "'":
                k = j + 1
                while k < n and text[k] != "'":
                    k += 1
                j = min(k + 1, n)
            else:
                match = _NAME_RE.match(text, j)
                j = match.end() if match else j
            tokens.append(Token("name", text[i:j], i))
            i = j
            continue

        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            match = _NUMBER_RE.match(text, i)
            assert match is not None
            tokens.append(Token("number", match.group(0), i))
            i = match.end()
            continue

        match = _NAME_RE.match(text, i)
        if match:
            j = match.end()
            k = j
            while k < n and text[k].isspace():
                k += 1
            kind = "func" if k < n and text[k] == "(" else "name"
            tokens.append(Token(kind, match.group(0), i))
            i = j
            continue

        if ch == ";":
            tokens.append(Token("sep", ch, i))
        elif ch == "(":
            tokens.append(Token("lparen", ch, i))
        elif ch == ")":
            tokens.append(Token("rparen", ch, i))
        elif ch in "{}|":
            tokens.append(Token("brace", ch, i))
        elif text[i : i + 2] in _TWO_CHAR_OPS:
            tokens.append(Token("op", text[i : i + 2], i))
            i += 2
            continue
        elif ch in _ONE_CHAR_OPS:
            tokens.append(Token("op", ch, i))
        else:
            tokens.append(Token("other", ch, i))
        i += 1

    return tokens


def call_contexts(tokens: list[Token]) -> list[CallContext | None]:
    """For each token, the innermost enclosing call and argument index.

    ``SUM(1; ROUND(A; 2))`` gives the ``2`` a context of ``("ROUND", 1)``.
    Parenthesized sub-expressions push a context with ``name=None``.
    """
    out: list[CallContext | None] = []
    stack: list[list[object]] = []
    prev: Token | None = None

    for token in tokens:
        current = (
            CallContext(name=stack[-1][0], arg_index=stack[-1][1])  # type: ignore[arg-type]
            if stack
            else None
        )
        if token.kind == "lparen":
            out.append(current)
            name = prev.text.upper() if prev is not None and prev.kind == "func" else None
            stack.append([name, 0])
        elif token.kind == "rparen":
            out.append(current)
            if stack:
                stack.pop()
        elif token.kind == "sep":
            out.append(current)
            if stack:
                stack[-1][1] = int(stack[-1][1]) + 1  # type: ignore[call-overload]
        else:
            out.append(current)

        if token.kind != "ws":
            prev = token

    return out


def iter_references(formula: str) -> list[Token]:
    """Convenience: just the reference tokens of a formula."""
    return [t for t in lex(formula) if t.kind == "ref" and t.ref is not None]
