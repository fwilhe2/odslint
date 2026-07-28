from __future__ import annotations

import pytest

from helpers import build, cells, fixture, formula, num, run_rule, txt
from odslint.loader import load
from odslint.rules.number_stored_as_text import classify

RULE = "data/number-stored-as-text"


def test_flags_text_numbers_and_text_dates():
    doc = load(fixture("text_numbers.fods"))
    assert cells(run_rule(doc, RULE)) == ["Import!A2", "Import!B2", "Import!A4"]


def test_codes_and_real_text_are_left_alone():
    doc = load(fixture("text_numbers.fods"))
    reported = cells(run_rule(doc, RULE))
    assert "Import!B3" not in reported  # "00742" — the leading zero matters
    assert "Import!B4" not in reported  # "n/a"
    assert "Import!A1" not in reported  # header
    assert "Import!A3" not in reported  # already numeric


def test_dates_can_be_accepted():
    doc = load(fixture("text_numbers.fods"))
    assert cells(run_rule(doc, RULE, check_dates=False)) == ["Import!A2", "Import!A4"]


def test_numeric_cells_and_formulas_are_never_flagged(tmp_path):
    doc = build(tmp_path, {"S": [[num(12.5), formula("=[.A1]")]]})
    assert run_rule(doc, RULE) == []


def test_message_names_the_value(tmp_path):
    doc = build(tmp_path, {"S": [[txt("42")]]})
    (found,) = run_rule(doc, RULE)
    assert "'42'" in found.message
    assert "SUM" in found.hint


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1234.56", "number"),
        ("1.234,56", "number"),
        ("1,234.56", "number"),
        ("-12", "number"),
        ("42 €", "number"),
        ("12,5%", "number"),
        (" 7 ", "number"),
        ("2024-01-31", "date"),
        ("31.01.2024", "date"),
        ("31/01/24", "date"),
        ("00742", None),
        ("0.5", "number"),
        ("1234567890123456789", None),
        ("n/a", None),
        ("1.2.3", None),
        ("A1", None),
        ("", None),
        ("   ", None),
        # A space groups thousands in some locales, but only in groups of three.
        ("1 234", "number"),
        ("1 234 567", "number"),
        ("12 34", None),
        ("12 34 56", None),
    ],
)
def test_classify(text, expected):
    assert classify(text) == expected


# -- fixes ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1234", 1234.0),
        ("-12", -12.0),
        ("+7", 7.0),
        ("12.5", 12.5),
        ("12,5", 12.5),  # one separator, not three digits after: a decimal
        ("1.234.567", 1234567.0),  # repeated separator: only grouping fits
        ("1,234,567", 1234567.0),
        ("1.234,56", 1234.56),  # rightmost separator wins
        ("1,234.56", 1234.56),
    ],
)
def test_parse_number_reads_unambiguous_values(text, expected):
    from odslint.rules.number_stored_as_text import parse_number

    assert parse_number(text) == expected


@pytest.mark.parametrize("text", ["1,234", "1.234", "19%", "€5", "12 34", "", "n/a"])
def test_parse_number_refuses_anything_it_could_get_wrong(text):
    """A wrong number is worse than no fix: "1,234" is 1234 in en-US and 1.234
    in de-DE, and nothing in the cell settles it."""
    from odslint.rules.number_stored_as_text import parse_number

    assert parse_number(text) is None


def test_an_ambiguous_value_is_still_flagged_but_offers_no_fix(tmp_path):
    doc = build(tmp_path, {"S": [[txt("1,234")]]})
    found = run_rule(doc, RULE)
    assert len(found) == 1
    assert found[0].fix is None


def test_an_unambiguous_value_offers_an_unsafe_fix(tmp_path):
    from odslint.diagnostics import Applicability

    doc = build(tmp_path, {"S": [[txt("12.5")]]})
    found = run_rule(doc, RULE)
    assert len(found) == 1
    fix = found[0].fix
    assert fix is not None
    assert fix.applicability is Applicability.UNSAFE
    assert fix.edits[0].kind == "number"
    assert fix.edits[0].value == "12.5"


def test_a_whole_number_is_written_without_a_trailing_zero(tmp_path):
    doc = build(tmp_path, {"S": [[txt("1234")]]})
    fix = run_rule(doc, RULE)[0].fix
    assert fix is not None
    assert fix.edits[0].value == "1234"


def test_text_dates_are_flagged_but_not_fixed(tmp_path):
    """Converting a date needs a number-format style, which the fixer does not
    write, so the diagnostic stands alone."""
    doc = build(tmp_path, {"S": [[txt("2024-01-15")]]})
    found = run_rule(doc, RULE)
    assert len(found) == 1
    assert "date" in found[0].message
    assert found[0].fix is None
