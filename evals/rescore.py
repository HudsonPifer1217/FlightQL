"""Re-score saved eval runs with the current scorer. No API calls.

Each record keeps the final SQL the agent ran, so this re-executes that SQL and
the gold SQL against the local database and recomputes `correct` and
`first_try_correct`, then rewrites summary.json / summary.md in place.

    python evals/rescore.py evals/results/20261125-002937 [more dirs...]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from flightql.config import Settings  # noqa: E402
from flightql.db import connect_readonly, run_query  # noqa: E402
from run_evals import EXACT, gold_results, summarize, to_markdown  # noqa: E402
from scoring import results_match  # noqa: E402

QUESTION_FILES = [ROOT / "evals" / "questions.yaml", ROOT / "evals" / "questions_hard.yaml"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+", type=Path)
    args = ap.parse_args()

    qs = {q["id"]: q for f in QUESTION_FILES for q in yaml.safe_load(f.read_text())["questions"]}
    con = connect_readonly(Settings().db_path)
    golds = gold_results(con, list(qs.values()))
    pred_cache: dict[str, list] = {}

    for d in args.dirs:
        path = d / "records.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines() if line]
        changed = []
        for r in records:
            q = qs[r["qid"]]
            if q["category"] not in EXACT:
                continue
            ok = False
            if r["status"] == "answered" and r["sql"]:
                if r["sql"] not in pred_cache:
                    pred_cache[r["sql"]] = run_query(con, r["sql"], 1000, 600).rows
                rows = pred_cache[r["sql"]]
                ok = any(results_match(g, rows, q.get("ordered", False), q.get("match"))
                         for g in golds[q["id"]])
            if ok != r["correct"]:
                changed.append(f"{r['setup']} run {r['run']} {r['qid']}: "
                               f"{'PASS' if r['correct'] else 'FAIL'} -> "
                               f"{'PASS' if ok else 'FAIL'}")
            r["correct"] = ok
            r["first_try_correct"] = ok and r["n_attempts"] == 1

        setups = list(dict.fromkeys(r["setup"] for r in records))
        summary = summarize(records, setups)
        path.write_text("".join(json.dumps(r, default=str) + "\n" for r in records))
        (d / "summary.json").write_text(json.dumps(summary, indent=2))
        (d / "summary.md").write_text(to_markdown(summary) + "\n")
        print(f"{d}: {len(changed)} verdict(s) changed")
        for c in changed:
            print("  " + c)


if __name__ == "__main__":
    main()
