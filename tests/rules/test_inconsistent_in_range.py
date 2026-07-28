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


# -- fixes ------------------------------------------------------------------


def test_the_outlier_is_rewritten_as_the_majority_filled_to_its_row():
    from odslint.diagnostics import Applicability

    doc = load(fixture("inconsistent_formulas.fods"))
    found = run_rule(doc, RULE)
    assert len(found) == 1

    fix = found[0].fix
    assert fix is not None
    assert fix.applicability is Applicability.UNSAFE
    # D2 reads =SUM([.A2:.C2]); at D4 that becomes =SUM([.A4:.C4]).
    assert fix.edits[0].formula == "=SUM([.A4:.C4])"
    assert (fix.edits[0].row, fix.edits[0].col) == (3, 3)


def test_no_fix_when_the_majority_formula_cannot_be_moved(tmp_path):
    """A block whose exemplar holds a dead reference has no trustworthy fill."""
    doc = build(
        tmp_path,
        {
            "S": [
                [num(1), formula("=[#REF!]+1")],
                [num(2), formula("=[#REF!]+1")],
                [num(3), formula("=[#REF!]+1")],
                [num(4), formula("=99")],
            ]
        },
    )
    found = run_rule(doc, RULE)
    assert len(found) == 1
    assert found[0].fix is None
