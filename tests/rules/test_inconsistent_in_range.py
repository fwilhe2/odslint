from __future__ import annotations

from helpers import EMPTY, build, cells, fixture, formula, num, run_rule
from odslint.loader import load

RULE = "formula/inconsistent-in-range"


def test_flags_the_odd_formula_in_a_column_block():
    doc = load(fixture("inconsistent_formulas.fods"))
    (found,) = run_rule(doc, RULE)
    assert found.location == "Totals!D4"
    assert "D2:D6" in found.message
    assert "'=SUM([.A2:.C2])'" in found.hint


def test_flags_the_odd_formula_in_a_row_block(tmp_path):
    doc = build(
        tmp_path,
        {
            "S": [
                [num(1), num(2), num(3), num(4)],
                [
                    formula("=[.A1]*2"),
                    formula("=[.B1]*2"),
                    formula("=[.C1]*3"),
                    formula("=[.D1]*2"),
                ],
            ]
        },
    )
    assert cells(run_rule(doc, RULE)) == ["S!C2"]


def test_consistent_block_is_silent(tmp_path):
    doc = build(
        tmp_path,
        {"S": [[formula("=[.B1]")], [formula("=[.B2]")], [formula("=[.B3]")]]},
    )
    assert run_rule(doc, RULE) == []


def test_heterogeneous_block_has_no_majority_to_break(tmp_path):
    doc = build(
        tmp_path,
        {"S": [[formula("=[.B1]")], [formula("=[.B2]*2")], [formula("=SUM([.B3:.C3])")]]},
    )
    assert run_rule(doc, RULE) == []


def test_runs_shorter_than_min_run_are_ignored(tmp_path):
    doc = build(tmp_path, {"S": [[formula("=[.B1]")], [formula("=[.B2]*2")]]})
    assert run_rule(doc, RULE) == []
    assert cells(run_rule(doc, RULE, min_run=2, majority_ratio=0.5)) != []


def test_a_gap_splits_the_block(tmp_path):
    # The deviating formula sits in its own run, so there is nothing to compare.
    doc = build(
        tmp_path,
        {
            "S": [
                [formula("=[.B1]")],
                [formula("=[.B2]")],
                [formula("=[.B3]")],
                [EMPTY],
                [formula("=[.B5]*2")],
            ]
        },
    )
    assert run_rule(doc, RULE) == []


def test_a_cell_in_both_a_row_and_column_run_is_reported_once(tmp_path):
    doc = build(
        tmp_path,
        {
            "S": [
                [formula("=1+1"), formula("=1+1"), formula("=1+1")],
                [formula("=1+1"), formula("=1+1"), formula("=1+1")],
                [formula("=1+1"), formula("=2+2"), formula("=1+1")],
                [formula("=1+1"), formula("=1+1"), formula("=1+1")],
            ]
        },
    )
    assert cells(run_rule(doc, RULE)) == ["S!B3"]


def test_majority_ratio_is_configurable(tmp_path):
    # Three shapes: 2 x "*2", 1 x "*3", 1 x "*4" -> 50% majority.
    doc = build(
        tmp_path,
        {
            "S": [
                [formula("=[.B1]*2")],
                [formula("=[.B2]*2")],
                [formula("=[.B3]*3")],
                [formula("=[.B4]*4")],
            ]
        },
    )
    assert run_rule(doc, RULE) == []
    assert len(run_rule(doc, RULE, majority_ratio=0.5)) == 2
