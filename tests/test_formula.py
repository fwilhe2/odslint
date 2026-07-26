"""Tokenizer, reference parsing, and R1C1 normalization."""

from __future__ import annotations

import pytest

from odslint.formula import lex, normalize_r1c1, parse_range_address, resolve
from odslint.formula.lexer import call_contexts, strip_prefix
from odslint.formula.reference import parse_reference


def kinds(formula: str) -> list[str]:
    return [t.kind for t in lex(formula) if t.kind != "ws"]


def test_strip_prefix_handles_both_forms():
    assert strip_prefix("of:=SUM([.A1])") == "=SUM([.A1])"
    assert strip_prefix("=SUM([.A1])") == "=SUM([.A1])"


def test_basic_tokenization():
    assert kinds("of:=SUM([.A1:.B2];2)") == [
        "func",
        "lparen",
        "ref",
        "sep",
        "number",
        "rparen",
    ]


def test_semicolon_inside_a_string_is_not_an_argument_separator():
    tokens = lex('=IF([.A1]="a;b";1;2)')
    assert sum(1 for t in tokens if t.kind == "sep") == 2
    assert [t.text for t in tokens if t.kind == "string"] == ['"a;b"']


def test_doubled_quote_escape_inside_a_string():
    assert [t.text for t in lex('="it""s"') if t.kind == "string"] == ['"it""s"']


def test_named_expression_reads_as_a_name_not_a_function():
    assert kinds("=TaxRate*100") == ["name", "op", "number"]
    assert kinds("=$$'Head count'+1") == ["name", "op", "number"]


@pytest.mark.parametrize(
    ("inner", "sheet", "row", "col", "absolute"),
    [
        (".A1", None, 0, 0, False),
        (".$B$7", None, 6, 1, True),
        ("Sheet2.A1", "Sheet2", 0, 0, False),
        ("$'Sheet 2.old'.$A$1", "Sheet 2.old", 0, 0, True),
    ],
)
def test_single_cell_references(inner, sheet, row, col, absolute):
    ref = parse_reference(inner)
    assert ref.is_single_cell
    assert ref.start.sheet == sheet
    assert (ref.start.row, ref.start.col) == (row, col)
    assert ref.is_absolute is absolute


def test_range_reference_resolves_against_the_current_sheet():
    ref = parse_reference(".A1:.C3")
    assert str(resolve(ref, "Data")) == "Data!A1:C3"


def test_reversed_range_is_normalized():
    assert str(resolve(parse_reference(".C3:.A1"), "Data")) == "Data!A1:C3"


def test_dead_reference_is_invalid_not_a_crash():
    ref = parse_reference("#REF!")
    assert ref.invalid
    assert resolve(ref, "Data") is None


def test_external_reference_is_not_resolvable():
    ref = parse_reference("'file:///tmp/other.ods'#$Sheet1.A1")
    assert ref.external == "file:///tmp/other.ods"
    assert resolve(ref, "Data") is None


def test_whole_column_reference_is_recognized_but_unresolvable():
    ref = parse_reference(".A:.A")
    assert ref.is_whole_column
    assert resolve(ref, "Data") is None


def test_parse_range_address_for_named_ranges():
    assert str(parse_range_address("$Model.$B$2:$Model.$C$9")) == "Model!B2:C9"
    assert parse_range_address("") is None


def test_call_contexts_track_function_and_argument():
    tokens = lex("=SUM(1;ROUND([.A1];2))")
    contexts = call_contexts(tokens)
    numbers = [(t.text, contexts[i]) for i, t in enumerate(tokens) if t.kind == "number"]
    assert numbers[0][1].name == "SUM" and numbers[0][1].arg_index == 0
    assert numbers[1][1].name == "ROUND" and numbers[1][1].arg_index == 1


def test_r1c1_fingerprint_matches_across_positions():
    # The same fill, one row apart.
    assert normalize_r1c1("=SUM([.A2:.C2])", 1, 3) == normalize_r1c1("=SUM([.A3:.C3])", 2, 3)


def test_r1c1_fingerprint_distinguishes_a_different_shape():
    assert normalize_r1c1("=SUM([.A2:.C2])", 1, 3) != normalize_r1c1("=SUM([.A3:.B3])", 2, 3)


def test_r1c1_ignores_whitespace_and_case():
    assert normalize_r1c1("=sum( [.A2] )", 1, 3) == normalize_r1c1("=SUM([.A2])", 1, 3)


def test_absolute_reference_keeps_its_anchor_in_the_fingerprint():
    # An absolute ref is the same cell everywhere, so the fingerprints differ
    # from a relative one that happens to point at the same place.
    assert normalize_r1c1("=[.$A$2]", 1, 3) != normalize_r1c1("=[.A2]", 1, 3)


def test_lexer_never_raises_on_garbage():
    for formula in ("=[", '="unterminated', "=SUM(", "=@#$", "="):
        assert isinstance(lex(formula), list)
