# FlightQL

**Ask plain-English questions about 37.8 million U.S. flights. Claude writes the SQL; FlightQL checks it against the real schema and read-only guardrails before anything runs, then answers with the result, the exact SQL, and the assumptions it made.**

![FlightQL answering "Which 5 airports had the worst average arrival delays in 2024?": SQL, assumptions, results, and summary](docs/demo.gif)

A trickier one, where the data's semantics matter:

```
$ python -m flightql "Averaged over every completed flight in 2023, how many minutes of weather delay did a flight have?"
[attempt 1: ok]
status: answered

SQL:
SELECT
  ROUND(AVG(COALESCE(weather_delay, 0)), 2) AS avg_weather_delay_minutes,
  COUNT(*) AS completed_flights
FROM flights
WHERE year = 2023 AND NOT cancelled AND NOT diverted
LIMIT 1000

Assumptions: weather_delay is only recorded when arr_delay >= 15, so a NULL on a completed
flight is treated as 0 minutes of weather delay...

avg_weather_delay_minutes | completed_flights
0.74 | 6743404

Averaged across all 6,743,404 completed flights in 2023, each flight had about 0.74 minutes of weather delay.
(6.6s, 2157 in / 587 out tokens, $0.0204)
```

That `COALESCE` is the interesting part. Without the project's data dictionary, the same question gets `AVG(weather_delay)` = **3.61 minutes**, which is 5× too high because it silently averages only the late flights. [See the evaluation](#what-the-evidence-says).

## Results at a glance

- **49 benchmark questions** (34 standard + 15 adversarially hard), **222 scored runs** against the real 37.8M-row database, every failure reviewed by hand.
- **100% execution accuracy** on both tiers for Claude Opus 5.5 and Claude Haiku 5.5 when given the data dictionary. **All 10 unanswerable and prompt-injection questions** were refused in every run, and the `flights` table was verified unchanged after every run.
- **The data dictionary is the accuracy lever:** removing it dropped Haiku to 87% on the hard tier (one error in 3/3 runs, another in 2/3).
- **Haiku matched Opus at ~1/30th the cost** ($0.0004 vs $0.0128 per question).
- **Guardrails had zero false blocks** across all 222 runs. 105 unit tests (validator, sandbox, retry loop, API, scorer, gold SQL) run in CI with a mocked LLM.

## How it works

Nothing reaches the database until it passes validation.

1. **Pick context.** The schema is read from DuckDB's `information_schema`, including the data's actual date range, and paired with a hand-written data dictionary (BTS columns are cryptic: times are `HHMM` integers, delay-cause minutes exist only for late flights, `day_of_week` 1 = Monday). Only columns relevant to the question are sent, plus sample rows.
2. **Generate.** Claude must return JSON matching a Pydantic model: `answerable`, `sql`, `tables_used`, `columns_used`, `assumptions`, `explanation` (structured outputs).
3. **Validate** with `sqlglot`. Reject if:
   - any table or column doesn't exist in the real schema (hallucination check, with did-you-mean hints)
   - it isn't exactly one read-only `SELECT` (no `DROP`/`DELETE`/`ATTACH`/`COPY`/`PRAGMA`/`SET`, no stacked statements)
   - it calls file/system/nested-SQL functions (`read_csv`, `query`, `getenv`, `glob`, ...)
   - the columns it actually reads don't match the `columns_used` it declared (honesty check)
4. **Retry.** On a validation or execution error, the failed SQL and the exact error go back to the model, up to 2 retries. After that, FlightQL says it couldn't answer instead of guessing.
5. **Execute** on a sandboxed connection: `read_only`, `enable_external_access=false`, settings locked, enforced `LIMIT`, query timeout. This is a second guardrail layer: SQL that slipped past the validator still can't write or read files.
6. **Answer** with a plain-English summary, the result table, the exact SQL, and stated assumptions.

Every step (context shown, raw model output, validation result, retries, latency, tokens, cost) is logged as one JSON line per question in `logs/flightql.jsonl`.

```
question ─► retrieve schema ─► Claude (JSON) ─► validate ──fail──► feed error back (≤2x)
                                                   │ok
                                                   ▼
                                   read-only DuckDB (LIMIT, timeout) ─► summary + SQL + rows
```

## Evaluation

Two hand-written question sets, each with gold SQL checked against the real data:

- **Standard tier** (`evals/questions.yaml`, 34 questions): simple lookups (8), aggregates (8), joins/filters (6), ambiguous wording (4), unanswerable (4), adversarial/prompt injection (4).
- **Hard tier** (`evals/questions_hard.yaml`, 15 questions), each built around one trap. Before a question was added, the gold answer and the plausible wrong answer were both computed to confirm the trap actually separates them:

| Trap | Right vs. plausible-wrong answer |
|---|---|
| `weather_delay` is NULL for on-time flights | 0.74 vs 3.61 min |
| Crossing midnight (`dep_time = 2400`, 24h+ delays) | 42,366 vs 41,160 flights |
| "Chicago" means two airports | 9.07 (ORD+MDW) vs 9.88 (ORD only) |
| `day_of_week` 1 = Monday | Saturday 2.94% vs Sunday 2.77% |
| Misspelled airline ("Souhtwest"), year-over-year change, rank-within-group, median, set comparison across years | |
| Dates outside the data (2019), a column that doesn't exist (aircraft model) | must refuse, not answer "0" |

**Scoring** (`evals/scoring.py`, unit-tested) compares result sets, not SQL text: column names, column order, row order (unless top-N), rounding and fraction-vs-percent don't matter. Ambiguous questions pass only if the answer states its interpretation. Unanswerable and adversarial questions pass only if refused or blocked. Gold top-N answers were checked for ties at the cutoff, and one tied question is scored as a set.

### Standard tier, 34 questions (1 run per setup)

| Metric | Haiku 5.5 · relevant columns | Opus 5.5 · relevant columns | Opus 5.5 · full schema |
|---|---|---|---|
| Execution accuracy (22 exact questions) | 100% | 100% | 100% |
| Valid SQL on first try | 100% | 100% | 100% |
| Hallucinated table/column rate | 0% | 0% | 0% |
| Ambiguous: answered with stated assumptions | 4/4 | 4/4 | 4/4 |
| Unanswerable: correctly refused | 4/4 | 4/4 | 4/4 |
| Adversarial: refused or blocked | 4/4 | 4/4 | 4/4 |
| Avg latency | 2.8 s | 3.9 s | 3.8 s |
| Avg cost per question | **$0.0004** | $0.0128 | $0.0215 |

### Hard tier, 15 questions: data-dictionary ablation

| Setup | Accuracy (13 exact) | Refusals (2) | Cost / question |
|---|---|---|---|
| Haiku 5.5 + dictionary (3 runs) | **100% ± 0%** | 2/2 | $0.0006 |
| Haiku 5.5, no dictionary (3 runs) | **87% ± 4%** (h10 wrong in 3/3, h09 in 2/3) | 2/2 | $0.0006 |
| Opus 5.5 + dictionary (1 run) | **100%** | 2/2 | $0.0155 |
| Opus 5.5, no dictionary (1 run) | 92% as auto-scored, 100% after review* | 2/2 | $0.0151 |

\*Both Opus failures in the original run were grader false negatives, not model errors: the right number inside a 3-row ROLLUP breakdown, and the hour written as the time block `'2000-2059'`. The second is now an accepted answer; the first stays strict. See below.

Raw per-question records (SQL, result rows, attempts, cost) are in `evals/results/`.

### What the evidence says

- **Semantic grounding mattered; schema grounding didn't have to.** In 222 runs, no first attempt named a table or column that doesn't exist, and no question needed a retry. On this schema, current Claude models don't hallucinate columns. The validator and retry loop are a safety net, not what drove accuracy. In live runs the model refused every injection attempt itself, so the validator never had to fire. Its blocking (stacked statements, `DELETE`, `read_csv`, `ATTACH`, ...) is proven by the unit tests, which feed it hostile SQL directly. What did change answers was the data dictionary: telling the model what a NULL means.
- **The small model is good enough here.** Haiku matched Opus on every question in both tiers at about 1/30th the cost and with lower latency. For this workload, the evidence supports Haiku plus the dictionary as the default.
- **Sending only the relevant columns cost nothing in accuracy** and cut Opus cost per question by 40% ($0.0128 vs $0.0215).
- **Limits of this evidence:** the standard tier ran once per setup and the Opus hard tier once, so those cells have no spread. The question sets are small (49) and written by one person. 100% on them is evidence that these question types work, not that every question will.

### Failures found and fixed

1. **Stale grounding.** The first live run, on a one-month dev database, said "across the full Jan 2020 – Nov 2025 period" because that range was hardcoded in the table description. **Fix:** the date range and row count are now read from the data when the catalog loads, applying the same principle as the schema check.
2. **NULL semantics (the real model failure).** Without the dictionary, Haiku answered the weather-delay question with `AVG(weather_delay)` = 3.61, in all 3 runs. The column is NULL unless a flight was 15+ minutes late, so this averages only late flights. With the dictionary note, the same model wrote `COALESCE(weather_delay, 0)` = 0.74 and explained why. **Fix:** this is why the dictionary exists. The ablation keeps measuring it.
3. **The grader was wrong, twice.** Two auto-scored "failures" were correct answers in an unexpected shape. One (hour vs. time block) is now an accepted alternative answer. The other (a ROLLUP breakdown when one number was asked for) stays strict and is reported instead of loosening the grader. **Lesson:** automatic grading needs human review of every failure before a number is published.
4. **The grader was too lenient, too.** A code review found the scorer gave every number a flat ±0.06 tolerance, so any two small rates matched (1.8% "equalled" 4.5%), and counts were allowed to be 1% off. **Fix:** counts must match exactly, and rounding is accepted only to the precision the model actually rounded to. Re-scoring every saved run (`evals/rescore.py`, no API calls) flipped two verdicts: Haiku without the dictionary had counted 139 flights that left the gate and were later cancelled as "departed late" (h09). That also revealed the dictionary itself was wrong: it claimed `dep_delay` is NULL for every cancelled flight, but 18,078 have one. The note is corrected.
5. **A tie in the gold answer.** For "top 5 routes by cancellation rate", ranks 4 and 5 tie at 8.29%, so a correct answer could fail on order alone. **Fix:** found by checking every top-N gold answer for ties before running. That question is now scored as a set (rank 6 is a clear 8.02%).

Design decisions and the full run log are in [`DECISIONS.md`](DECISIONS.md).

## Run it

```bash
pip install -e ".[dev]"                  # 1. install
python scripts/build_db.py               # 2. download BTS data 2020-2025 into data/flights.duckdb (~2 GB, resumable, ~1 hour)
cp .env.example .env                     #    add ANTHROPIC_API_KEY
python -m flightql "Which airline had the most flights in 2023?"   # 3. ask
```

- **API:** `uvicorn flightql.api:app`, then `POST /ask {"question": "..."}` (interactive docs at `/docs`).
- **Tests:** `pytest`. A synthetic database and a fake LLM, so no data, no API key, and no cost. They run on every push via GitHub Actions.
- **Evals:**
  - `python evals/run_evals.py --check-gold` validates all gold SQL with no API calls.
  - `python evals/run_evals.py --setups claude-haiku-5-5:retrieved claude-haiku-5-5:retrieved:nodict --runs 3 --max-cost 1.00 --questions evals/questions_hard.yaml` runs a benchmark. Setups are `MODEL:MODE[:RETRIES][:nodict]`, and `--max-cost` is a hard spending cap.

## Data

[BTS Airline On-Time Performance](https://www.transtats.bts.gov/), Jan 2020 – Nov 2025, all reporting carriers: 37,759,151 scheduled flights including cancellations and diversions (my earlier 36.3M-flight delay analysis excluded them).

| Table | Rows | What |
|---|---|---|
| `flights` | 37.8M | one row per scheduled flight, 38 columns |
| `airlines` | 18 | carrier code → name |
| `airports` | 385 | airport code → name, city, state, lat/long |

## Layout

```
flightql/
  schema.py     schema, data dictionary, and date coverage from DuckDB
  retrieve.py   relevant-column selection
  generate.py   prompt, Pydantic response model, Claude call
  validate.py   sqlglot checks and read-only guardrails
  db.py         sandboxed read-only execution with timeout
  agent.py      retry loop, execution, JSON-line logging
  api.py        FastAPI /ask, /schema, /health
evals/          questions.yaml, questions_hard.yaml, scoring.py, run_evals.py, results/
tests/          validator, sandbox, agent loop, API, gold SQL (LLM mocked)
scripts/        build_db.py
```
