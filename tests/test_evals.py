"""Every gold query must pass our own validator and run; the scorer behaves as documented."""
import sys
from pathlib import Path

import pytest
import yaml

from flightql.db import run_query
from flightql.validate import validate_sql

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))
from scoring import results_match  # noqa: E402

QUESTIONS = yaml.safe_load((ROOT / "evals" / "questions.yaml").read_text())["questions"]
HARD = yaml.safe_load((ROOT / "evals" / "questions_hard.yaml").read_text())["questions"]
GOLD = [(q["id"], s) for q in QUESTIONS + HARD
        for s in [q.get("gold"), *q.get("alt_gold", [])] if s]


def test_benchmark_shape():
    cats = [q["category"] for q in QUESTIONS]
    assert len(QUESTIONS) >= 30
    assert len({q["id"] for q in QUESTIONS}) == len(QUESTIONS)
    for c in ("simple", "aggregate", "join", "ambiguous", "unanswerable", "adversarial"):
        assert cats.count(c) >= 4, c
    for q in QUESTIONS:
        if q["category"] in ("simple", "aggregate", "join"):
            assert q.get("gold"), q["id"]


@pytest.mark.parametrize("qid,sql", GOLD)
def test_gold_sql_validates_and_runs(catalog, con, qid, sql):
    v = validate_sql(sql, catalog, max_rows=100_000)
    assert v.ok, (qid, v.errors)
    run_query(con, v.sql, 10, 30)


def test_scorer_ignores_column_names_order_and_rounding():
    gold = [("SFO", 12.3456), ("LAX", 8.0)]
    assert results_match(gold, [[8.0, "lax"], [12.35, "SFO"]])            # order, case, ROUND
    assert results_match(gold, [["SFO", 12.35, 999], ["LAX", 8.0, 1]])    # extra column ok
    assert not results_match(gold, [["SFO", 12.35]])                       # missing row
    assert not results_match(gold, [["SFO", 20.0], ["LAX", 8.0]])          # wrong value


def test_scorer_accepts_percent_for_fraction():
    assert results_match([(2020, 0.0412)], [[2020, 4.12]])


def test_scorer_ordered_top_n():
    gold = [("A",), ("B",)]
    assert results_match(gold, [["A", 5], ["B", 4], ["C", 3]], ordered=True)
    assert not results_match(gold, [["B", 5], ["A", 4]], ordered=True)


def test_scorer_match_subset_of_columns():
    gold = [("A", 10), ("B", 20)]
    assert results_match(gold, [["carrier", 10], ["weather", 20]], match=[1])


def test_scorer_rejects_wrong_small_rates():
    # Regression: a flat 0.06 absolute tolerance used to make any two small
    # fractions "equal", and the x0.01 rescale let 6.0 match 0.018.
    assert not results_match([(0.018,)], [[0.045]])
    assert not results_match([(0.018,)], [[6.0]])
    assert not results_match([(0.018,)], [[0.0]])     # rounded away entirely
    assert results_match([(0.018,)], [[0.02]])        # ROUND(x, 2) still fine
    assert results_match([(0.018,)], [[1.8]])         # as a percentage


def test_scorer_rounding_tolerance_follows_predicted_precision():
    assert results_match([(12.3456,)], [[12.3]])      # ROUND(x, 1)
    assert results_match([(12.3456,)], [[12]])        # int vs float: within 1%
    assert not results_match([(12.3456,)], [[12.21]])  # >1% off, not a rounding of gold


def test_scorer_counts_are_exact():
    assert results_match([(1_438_465,)], [[1_438_465]])
    assert not results_match([(1_438_465,)], [[1_440_000]])  # within 1%, still wrong
