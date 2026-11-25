"""Run the FlightQL benchmark.

Each setup is MODEL:SCHEMA_MODE[:RETRIES]. Every question runs --runs times per
setup (LLM output varies), and metrics are reported as mean +/- std across runs.

    python evals/run_evals.py --check-gold                  # validate gold SQL, no API calls
    python evals/run_evals.py --setups claude-opus-5-5:retrieved claude-haiku-5-5:retrieved
    python evals/run_evals.py --setups claude-opus-5-5:full claude-opus-5-5:retrieved --runs 3
    python evals/run_evals.py --only s01 a02 --runs 1       # quick spot check

Outputs go to evals/results/<timestamp>/: records.jsonl (one line per question per
run), summary.json and summary.md (the table for the README).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

from flightql.agent import AgentResult, FlightQLAgent  # noqa: E402
from flightql.config import Settings  # noqa: E402
from flightql.db import connect_readonly, run_query  # noqa: E402
from flightql.generate import AnthropicLLM  # noqa: E402
from flightql.schema import load_catalog  # noqa: E402
from flightql.validate import validate_sql  # noqa: E402
from scoring import results_match  # noqa: E402

EXACT = {"simple", "aggregate", "join", "hard"}
GOLD_MAX_ROWS = 100_000


def load_questions(path: Path, only: list[str] | None) -> list[dict]:
    qs = yaml.safe_load(path.read_text())["questions"]
    return [q for q in qs if not only or q["id"] in only]


def gold_results(con, qs: list[dict]) -> dict[str, list[list[tuple]]]:
    out = {}
    for q in qs:
        if q["category"] in EXACT:
            sqls = [q["gold"], *q.get("alt_gold", [])]
            out[q["id"]] = [run_query(con, s, GOLD_MAX_ROWS, 600).rows for s in sqls]
    return out


def check_gold(con, qs: list[dict]) -> int:
    catalog = load_catalog(con)
    bad = 0
    for q in qs:
        for s in [q.get("gold"), *q.get("alt_gold", [])]:
            if not s:
                continue
            v = validate_sql(s, catalog, GOLD_MAX_ROWS)
            if not v.ok:
                bad += 1
                print(f"{q['id']}: INVALID {v.errors}")
                continue
            r = run_query(con, v.sql, 10, 600)
            print(f"{q['id']}: ok  {len(r.rows)} row(s)  first={r.rows[0] if r.rows else None}")
    return bad


@dataclass
class Score:
    correct: bool
    first_try_correct: bool
    first_try_valid: bool
    first_try_hallucinated: bool
    produced_sql_first_try: bool


def score(q: dict, r: AgentResult, golds: dict) -> Score:
    cat = q["category"]
    a1 = r.attempts[0] if r.attempts else None
    produced = bool(a1 and a1.sql)
    first_valid = bool(produced and not a1.validation_errors and not a1.execution_error)
    halluc = bool(a1 and a1.hallucinated_schema)

    if cat in EXACT:
        ok = r.status == "answered" and any(
            results_match(g, r.rows, q.get("ordered", False), q.get("match"))
            for g in golds[q["id"]])
    elif cat == "ambiguous":
        ok = r.status == "answered" and bool(r.assumptions.strip())
    elif cat == "unanswerable":
        ok = r.status == "refused"
    elif cat == "adversarial":
        ok = r.status in ("refused", "failed") or (q.get("benign_ok", False)
                                                   and r.status == "answered")
    else:
        raise ValueError(cat)
    # "First try" = the answer we'd have had with retries disabled.
    first_ok = ok and len(r.attempts) == 1
    return Score(ok, first_ok, first_valid, halluc, produced)


def _mean_std(xs: list[float]) -> tuple[float, float]:
    if not xs:
        return float("nan"), float("nan")
    return statistics.mean(xs), (statistics.stdev(xs) if len(xs) > 1 else 0.0)


def summarize(records: list[dict], setups: list[str]) -> dict:
    summary = {}
    for setup in setups:
        recs = [r for r in records if r["setup"] == setup]
        runs = sorted({r["run"] for r in recs})
        per_run: dict[str, list[float]] = {}

        def add(name, vals):
            if vals:
                per_run.setdefault(name, []).append(sum(vals) / len(vals))

        for run in runs:
            rr = [r for r in recs if r["run"] == run]
            exact = [r for r in rr if r["category"] in EXACT]
            add("execution_accuracy", [r["correct"] for r in exact])
            add("first_try_accuracy", [r["first_try_correct"] for r in exact])
            sql_q = [r for r in rr if r["category"] in EXACT | {"ambiguous"}]
            add("valid_first_try", [r["first_try_valid"] for r in sql_q])
            produced = [r for r in rr if r["produced_sql_first_try"]]
            add("hallucinated_schema_rate", [r["first_try_hallucinated"] for r in produced])
            add("ambiguous_handled", [r["correct"] for r in rr if r["category"] == "ambiguous"])
            add("correct_refusals", [r["correct"] for r in rr if r["category"] == "unanswerable"])
            add("adversarial_handled",
                [r["correct"] for r in rr if r["category"] == "adversarial"])
            add("avg_latency_s", [r["latency_s"] for r in rr])
            add("avg_cost_usd", [r["cost_usd"] for r in rr])
            add("avg_attempts", [r["n_attempts"] for r in rr])
        summary[setup] = {k: _mean_std(v) for k, v in per_run.items()}
        summary[setup]["n_runs"] = (len(runs), 0)
        summary[setup]["n_questions"] = (len({r["qid"] for r in recs}), 0)
    return summary


def to_markdown(summary: dict) -> str:
    rows = [
        ("Execution accuracy (simple/aggregate/join)", "execution_accuracy", "pct"),
        ("...without retries (first attempt only)", "first_try_accuracy", "pct"),
        ("Valid SQL on first try", "valid_first_try", "pct"),
        ("Hallucinated schema rate (first attempts)", "hallucinated_schema_rate", "pct"),
        ("Ambiguous: answered with stated assumptions", "ambiguous_handled", "pct"),
        ("Unanswerable: correctly refused", "correct_refusals", "pct"),
        ("Adversarial: refused or blocked", "adversarial_handled", "pct"),
        ("Avg attempts per question", "avg_attempts", "num"),
        ("Avg latency (s)", "avg_latency_s", "num"),
        ("Avg cost per question (USD)", "avg_cost_usd", "usd"),
    ]
    setups = list(summary)
    out = ["| Metric | " + " | ".join(f"`{s}`" for s in setups) + " |",
           "|---|" + "---|" * len(setups)]
    for label, key, kind in rows:
        cells = []
        for s in setups:
            m, sd = summary[s].get(key, (float("nan"), float("nan")))
            if m != m:
                cells.append("–")
            elif kind == "pct":
                cells.append(f"{m:.0%} ± {sd:.0%}")
            elif kind == "usd":
                cells.append(f"${m:.4f}")
            else:
                cells.append(f"{m:.2f} ± {sd:.2f}")
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    n_runs = summary[setups[0]]["n_runs"][0]
    n_q = summary[setups[0]]["n_questions"][0]
    out.append(f"\n{n_q} questions × {n_runs} runs per setup; mean ± std across runs.")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--setups", nargs="+", default=["claude-opus-5-5:retrieved"])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--questions", type=Path, default=ROOT / "evals" / "questions.yaml")
    ap.add_argument("--workers", type=int, default=4, help="questions run in parallel")
    ap.add_argument("--check-gold", action="store_true")
    ap.add_argument("--max-cost", type=float, default=None,
                    help="stop sending questions once estimated spend (USD) reaches this")
    args = ap.parse_args()

    settings = Settings()
    qs = load_questions(args.questions, args.only)
    con = connect_readonly(settings.db_path)

    if args.check_gold:
        sys.exit(1 if check_gold(con, qs) else 0)

    print(f"computing gold results for {len(qs)} questions...", flush=True)
    golds = gold_results(con, qs)
    flights_before = con.execute("SELECT COUNT(*) FROM flights").fetchone()[0]

    out_dir = ROOT / "evals" / "results" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True)
    records: list[dict] = []
    spent = {"usd": 0.0, "skipped": 0}
    lock = threading.Lock()

    def ask_within_budget(agent, q):
        # Checked before each question; in-flight questions may overshoot the cap by
        # at most `workers` questions' worth of cost.
        with lock:
            if args.max_cost is not None and spent["usd"] >= args.max_cost:
                spent["skipped"] += 1
                return q, None
        r = agent.ask(q["question"])
        with lock:
            spent["usd"] += r.cost_usd
        return q, r

    for setup in args.setups:
        # MODEL:MODE[:RETRIES][:nodict]
        model, mode, *rest = setup.split(":")
        retries = next((int(x) for x in rest if x.isdigit()), 2)
        s = Settings(model=model, schema_mode=mode, summarize=False, max_retries=retries,
                     data_dictionary="nodict" not in rest, log_dir=out_dir / "logs")
        agent = FlightQLAgent(AnthropicLLM(model), s, con=con)
        for run in range(1, args.runs + 1):
            print(f"\n== {setup} run {run}/{args.runs}  (spent so far ${spent['usd']:.2f})",
                  flush=True)
            with ThreadPoolExecutor(args.workers) as pool:
                results = list(pool.map(lambda q: ask_within_budget(agent, q), qs))
            for q, r in results:
                if r is None:
                    continue
                sc = score(q, r, golds)
                rec = {
                    "setup": setup, "run": run, "qid": q["id"], "category": q["category"],
                    "question": q["question"], "status": r.status, "sql": r.sql,
                    "assumptions": r.assumptions, "answer": r.answer,
                    "n_attempts": len(r.attempts), "latency_s": r.latency_s,
                    "cost_usd": r.cost_usd, "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens, **sc.__dict__,
                    "attempt_errors": [a.validation_errors or a.execution_error or a.llm_error
                                       for a in r.attempts],
                    "pred_rows_head": r.rows[:5],
                }
                records.append(rec)
                mark = "PASS" if sc.correct else "FAIL"
                print(f"  {q['id']:4} {mark}  {r.status:8} attempts={len(r.attempts)}",
                      flush=True)

    flights_after = con.execute("SELECT COUNT(*) FROM flights").fetchone()[0]
    assert flights_after == flights_before, "flights table changed during eval!"

    with open(out_dir / "records.jsonl", "w") as f:
        for rec in records:
            f.write(json.dumps(rec, default=str) + "\n")
    summary = summarize(records, args.setups)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    md = to_markdown(summary)
    (out_dir / "summary.md").write_text(md + "\n")
    print("\n" + md)
    print(f"\nestimated spend: ${spent['usd']:.2f} (cache reads billed at full price, so "
          "this is an upper bound)")
    if spent["skipped"]:
        print(f"WARNING: budget cap reached; {spent['skipped']} questions were skipped")
    print(f"\nflights row count unchanged: {flights_after:,}")
    print(f"results: {out_dir}")


if __name__ == "__main__":
    main()
