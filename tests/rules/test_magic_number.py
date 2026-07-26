from __future__ import annotations

import pytest

from helpers import build, cells, fixture, formula, num, run_rule
from odslint.loader import load

RULE = "formula/magic-number"


def test_flags_a_hardcoded_rate():
    doc = load(fixture("magic_numbers.fods"))
    found = run_rule(doc, RULE)
    assert cells(found) == ["Invoice!C2"]
    assert "1.19" in found[0].message


def test_structural_arguments_are_not_magic():
    # ROUND(...;2) and the "1.19" *string* in the fixture must stay silent.
    doc = load(fixture("magic_numbers.fods"))
    assert "Invoice!C3" not in cells(run_rule(doc, RULE))
    assert "Invoice!C4" not in cells(run_rule(doc, RULE))


@pytest.mark.parametrize(
    "expression",
    [
        "=[.A1]*0",
        "=[.A1]+1",
        "=[.A1]*-1",
        "=VLOOKUP([.A1];[.B1:.D9];3;0)",
        "=INDEX([.B1:.D9];2;3)",
        "=LEFT([.A1];4)",
        "=DATE(2024;1;31)",
    ],
)
def test_silent_cases(tmp_path, expression):
    doc = build(tmp_path, {"S": [[num(1), formula(expression)]]})
    assert run_rule(doc, RULE) == []


@pytest.mark.parametrize(
    ("expression", "literal"),
    [
        ("=[.A1]*1.19", "1.19"),
        ("=[.A1]*-2", "-2"),
        ("=ROUND([.A1]*1.07;2)", "1.07"),
        ("=IF([.A1];42;0)", "42"),
        ("=VLOOKUP([.A1];[.B1:.D9];3;0)*1.5", "1.5"),
    ],
)
def test_flagged_cases(tmp_path, expression, literal):
    doc = build(tmp_path, {"S": [[num(1), formula(expression)]]})
    (found,) = run_rule(doc, RULE)
    assert literal.lstrip("-") in found.message


def test_a_literal_is_reported_once_per_cell(tmp_path):
    doc = build(tmp_path, {"S": [[num(1), formula("=[.A1]*1.19+1.19")]]})
    assert len(run_rule(doc, RULE)) == 1


def test_allowed_values_are_configurable(tmp_path):
    doc = build(tmp_path, {"S": [[num(1), formula("=[.A1]*100")]]})
    assert len(run_rule(doc, RULE)) == 1
    assert run_rule(doc, RULE, allowed=[0, 1, -1, 100]) == []


def test_extra_structural_arguments_are_configurable(tmp_path):
    doc = build(tmp_path, {"S": [[num(1), formula("=ORG.OPENOFFICE.RAWSUBTRACT([.A1];7)")]]})
    assert len(run_rule(doc, RULE)) == 1
    assert run_rule(doc, RULE, structural_args=["ORG.OPENOFFICE.RAWSUBTRACT:1"]) == []


def test_subtraction_is_not_mistaken_for_a_negative_literal(tmp_path):
    # 5 here is subtracted, not a unary -5; either way it is magic, but the
    # message should name what the user typed.
    doc = build(tmp_path, {"S": [[num(1), formula("=[.A1]-5")]]})
    (found,) = run_rule(doc, RULE)
    assert "5" in found.message
