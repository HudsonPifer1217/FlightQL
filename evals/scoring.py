"""Scoring rules for the FlightQL benchmark.

Execution accuracy compares *results*, not SQL text. Two queries that compute
the same answer with different SQL, column names, or column order both pass.

A predicted result matches a gold result when:
  * row counts are equal (or, for ordered top-N questions, the first k predicted
    rows match the k gold rows), and
  * every gold column (or those listed in `match`) has a predicted column with the
    same values, text compared case-insensitively, and numbers compared as:
      - integers (counts) exactly;
      - other numbers within 1% relative, or equal to the gold value rounded to
        the predicted value's precision (so ROUND(x, 2) passes). Rounding is only
        excused when it keeps the value within 30% of gold, so a 1.8% rate
        returned as 0.0 or 0.045 still fails;
      - fractions also accepted as percentages (x100) and vice versa.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any


def _norm(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    if isinstance(v, Decimal):  # integral DECIMAL(n, 0) is a count; scaled ones are not
        return int(v) if v.as_tuple().exponent >= 0 else float(v)
    try:
        return float(v)  # numeric strings
    except (TypeError, ValueError):
        return str(v).strip().lower()


def _decimals(x: float) -> int | None:
    """Number of decimal places x appears to be rounded to (None if more than 6)."""
    for d in range(7):
        if abs(round(x, d) - x) <= 1e-9 * max(1.0, abs(x)):
            return d
    return None


def _num_eq(gold: float, pred: float) -> bool:
    diff = abs(gold - pred)
    if diff <= 0.01 * abs(gold) + 1e-12:
        return True
    d = _decimals(pred)
    if d is None:
        return False
    half_unit = 0.5 * 10 ** -d
    return diff <= half_unit + 1e-9 and half_unit <= 0.3 * abs(gold)


def _eq(gold: Any, pred: Any) -> bool:
    if gold is None or pred is None:
        return gold is None and pred is None
    num = (int, float)
    if isinstance(gold, int) and isinstance(pred, int):
        return gold == pred
    if isinstance(gold, num) and isinstance(pred, num):
        return _num_eq(float(gold), float(pred))
    return gold == pred


def _sort_key(v: Any):
    return (v is None, isinstance(v, str), v if v is not None else 0)


def _column_matches(gold: list, pred: list, ordered: bool) -> bool:
    if len(gold) != len(pred):
        return False
    for scale in (1.0, 100.0, 0.01):
        p = [x * scale if isinstance(x, float) else x for x in pred]
        g = gold
        if not ordered:
            try:
                g, p = sorted(g, key=_sort_key), sorted(p, key=_sort_key)
            except TypeError:
                return False
        if all(_eq(a, b) for a, b in zip(g, p)):
            return True
        if not any(isinstance(x, float) for x in pred):
            break  # integers and text are never rescaled
    return False


def results_match(gold_rows: list[tuple], pred_rows: list[list], ordered: bool = False,
                  match: list[int] | None = None) -> bool:
    if not gold_rows and not pred_rows:
        return True
    if not gold_rows or not pred_rows:
        return False
    k = len(gold_rows)
    if ordered and len(pred_rows) > k:
        pred_rows = pred_rows[:k]  # top-N: extra trailing rows are tolerated
    if len(pred_rows) != k:
        return False

    gold_cols = [[_norm(r[i]) for r in gold_rows] for i in range(len(gold_rows[0]))]
    pred_cols = [[_norm(r[i]) for r in pred_rows] for i in range(len(pred_rows[0]))]
    wanted = match if match is not None else range(len(gold_cols))
    used: set[int] = set()
    for gi in wanted:
        hit = next((pi for pi, pc in enumerate(pred_cols)
                    if pi not in used and _column_matches(gold_cols[gi], pc, ordered)), None)
        if hit is None:
            return False
        used.add(hit)
    return True
