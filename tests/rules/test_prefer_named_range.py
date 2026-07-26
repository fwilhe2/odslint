from __future__ import annotations

import pytest

from helpers import build, cells, fixture, formula, num, run_rule, txt
from odslint.loader import load
from odslint.rules.prefer_named_range import suggest_name

RULE = "formula/prefer-named-range"


@pytest.fixture
def diagnostics():
    return run_rule(load(fixture("named_ranges.fods")), RULE)


def test_flags_an_address_an_existing_name_already_covers(diagnostics):
    found = [d for d in diagnostics if d.location == "Model!D1"]
    assert len(found) == 1
    assert "TaxRate" in found[0].message
    assert found[0].hint == "replace [.$B$1] with TaxRate"


def test_flags_absolute_references_to_labelled_constants(diagnostics):
    found = [d for d in diagnostics if d.location == "Model!D2"]
    assert len(found) == 2
    hints = " ".join(d.hint for d in found)
    assert "'Hours'" in hints
    assert "'Rate_EUR_h'" in hints


def test_ignores_relative_references_to_neighbours(diagnostics):
    # =[.B2]*[.B3] in a fill block is not a naming failure.
    assert "Model!D3" not in cells(diagnostics)


def test_ignores_references_to_other_formulas(tmp_path):
    doc = build(
        tmp_path,
        {"S": [[num(2), formula("=[.A1]*2")], [formula("=[.$B$1]+1")]]},
    )
    assert run_rule(doc, RULE) == []


def test_cross_sheet_constant_is_flagged_even_when_relative(tmp_path):
    doc = build(
        tmp_path,
        {
            "Params": [[txt("Discount"), num(0.1)]],
            "Calc": [[formula("=[Params.B1]*100")]],
        },
    )
    (found,) = run_rule(doc, RULE)
    assert found.location == "Calc!A1"
    assert "Params.B1" in found.message
    assert "'Discount'" in found.hint


def test_unlabelled_constant_falls_back_to_a_generic_hint(tmp_path):
    doc = build(tmp_path, {"S": [[num(0.19)], [formula("=[.$A$1]*2")]]})
    (found,) = run_rule(doc, RULE)
    assert "Named Ranges" in found.hint


def test_option_can_disable_the_constant_check(tmp_path):
    doc = build(tmp_path, {"S": [[txt("Rate"), num(0.19)], [formula("=[.$B$1]*2")]]})
    assert run_rule(doc, RULE) != []
    assert run_rule(doc, RULE, flag_unnamed_constants=False) == []


def test_each_reference_is_reported_once_per_cell(tmp_path):
    doc = build(
        tmp_path,
        {"S": [[txt("Rate"), num(0.19)], [formula("=[.$B$1]+[.$B$1]")]]},
    )
    assert len(run_rule(doc, RULE)) == 1


def test_sheet_scoped_name_shadows_document_scope(tmp_path):
    doc = build(
        tmp_path,
        {"S": [[num(1)], [formula("=[.$A$1]")]]},
        named_ranges={"Start": "$S.$A$1"},
    )
    (found,) = run_rule(doc, RULE)
    assert "Start" in found.message


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Tax rate", "Tax_rate"),
        ("Rate (EUR/h)", "Rate_EUR_h"),
        ("  spaced  ", "spaced"),
        ("2024 budget", "_2024_budget"),
        ("%%%", None),
    ],
)
def test_suggest_name(label, expected):
    assert suggest_name(label) == expected
