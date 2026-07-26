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
