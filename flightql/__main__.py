"""Command line:  python -m flightql "Which airline had the most flights in 2023?" """
from __future__ import annotations

import argparse

from .agent import FlightQLAgent
from .config import Settings
from .generate import AnthropicLLM


def main() -> None:
    ap = argparse.ArgumentParser(prog="flightql")
    ap.add_argument("question")
    ap.add_argument("--model")
    ap.add_argument("--full-schema", action="store_true")
    ap.add_argument("--rows", type=int, default=15, help="result rows to print")
    args = ap.parse_args()

    s = Settings()
    if args.model:
        s.model = args.model
    if args.full_schema:
        s.schema_mode = "full"
    r = FlightQLAgent(AnthropicLLM(s.model), s).ask(args.question)

    for a in r.attempts:
        mark = "ok" if not (a.validation_errors or a.execution_error or a.llm_error) else "x"
        print(f"[attempt {a.n}: {mark}]", *(a.validation_errors or
              [e for e in (a.execution_error, a.llm_error) if e]))
    print(f"\nstatus: {r.status}")
    if r.sql:
        print(f"\nSQL:\n{r.sql}")
    if r.assumptions:
        print(f"\nAssumptions: {r.assumptions}")
    if r.columns:
        print("\n" + " | ".join(r.columns))
        for row in r.rows[: args.rows]:
            print(" | ".join(str(x) for x in row))
        if len(r.rows) > args.rows or r.truncated:
            print("...")
    print(f"\n{r.answer}")
    print(f"\n({r.latency_s:.1f}s, {r.input_tokens} in / {r.output_tokens} out tokens, "
          f"${r.cost_usd:.4f})")


if __name__ == "__main__":
    main()
