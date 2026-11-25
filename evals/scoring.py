"""Scoring rules for the FlightQL benchmark.

Execution accuracy compares *results*, not SQL text. Two queries that compute
the same answer with different SQL, column names, or column order both pass.

A predicted result matches a gold result when:
  * row counts are equal (or, for ordered top-N questions, the first k predicted
    rows match the k gold rows), and
  * every gold column (or those listed in `match`) has a predicted column with the
    same values: numbers within 1% (or 0.06 absolute, to allow ROUND(x, 1)),
    fractions also accepted as percentages (x100), text compared case-insensitively.
"""
from __future__ import annotations

import datetime as dt
from typing import Any


def _norm(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    try:
        return float(v)  # Decimal, numeric strings
    except (TypeError, ValueError):
        return str(v).strip().lower()


def _eq(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= max(0.01 * abs(a), 0.06)
    return a == b


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
            break
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
