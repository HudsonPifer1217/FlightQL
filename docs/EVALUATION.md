# FlightQL: Evaluation

How FlightQL was tested, what the numbers are, and what went wrong along the way.
Back to the [README](../README.md).

## The question sets

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

## Standard tier, 34 questions (1 run per setup)

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

## Hard tier, 15 questions: data-dictionary ablation

| Setup | Accuracy (13 exact) | Refusals (2) | Cost / question |
|---|---|---|---|
| Haiku 5.5 + dictionary (3 runs) | **100% ± 0%** | 2/2 | $0.0006 |
| Haiku 5.5, no dictionary (3 runs) | **87% ± 4%** (h10 wrong in 3/3, h09 in 2/3) | 2/2 | $0.0006 |
| Opus 5.5 + dictionary (1 run) | **100%** | 2/2 | $0.0155 |
| Opus 5.5, no dictionary (1 run) | 92% as auto-scored, 100% after review* | 2/2 | $0.0151 |

\*Both Opus failures in the original run were grader false negatives, not model errors: the right number inside a 3-row ROLLUP breakdown, and the hour written as the time block `'2000-2059'`. The second is now an accepted answer; the first stays strict. See below.

Raw per-question records (SQL, result rows, attempts, cost) are in `evals/results/`.

## What the evidence says

- **Semantic grounding mattered; schema grounding didn't have to.** In 222 runs, no first attempt named a table or column that doesn't exist, and no question needed a retry. On this schema, current Claude models don't hallucinate columns. The validator and retry loop are a safety net, not what drove accuracy. In live runs the model refused every injection attempt itself, so the validator never had to fire. Its blocking (stacked statements, `DELETE`, `read_csv`, `ATTACH`, ...) is proven by the unit tests, which feed it hostile SQL directly. What did change answers was the data dictionary: telling the model what a NULL means.
- **The small model is good enough here.** Haiku matched Opus on every question in both tiers at about 1/30th the cost and with lower latency. For this workload, the evidence supports Haiku plus the dictionary as the default.
- **Sending only the relevant columns cost nothing in accuracy** and cut Opus cost per question by 40% ($0.0128 vs $0.0215).
- **Limits of this evidence:** the standard tier ran once per setup and the Opus hard tier once, so those cells have no spread. The question sets are small (49) and written by one person. 100% on them is evidence that these question types work, not that every question will.

## Failures found and fixed

1. **Stale grounding.** The first live run, on a one-month dev database, said "across the full Jan 2020 – Nov 2025 period" because that range was hardcoded in the table description. **Fix:** the date range and row count are now read from the data when the catalog loads, applying the same principle as the schema check.
2. **NULL semantics (the real model failure).** Without the dictionary, Haiku answered the weather-delay question with `AVG(weather_delay)` = 3.61, in all 3 runs. The column is NULL unless a flight was 15+ minutes late, so this averages only late flights. With the dictionary note, the same model wrote `COALESCE(weather_delay, 0)` = 0.74 and explained why. **Fix:** this is why the dictionary exists. The ablation keeps measuring it.
3. **The grader was wrong, twice.** Two auto-scored "failures" were correct answers in an unexpected shape. One (hour vs. time block) is now an accepted alternative answer. The other (a ROLLUP breakdown when one number was asked for) stays strict and is reported instead of loosening the grader. **Lesson:** automatic grading needs human review of every failure before a number is published.
4. **The grader was too lenient, too.** A code review found the scorer gave every number a flat ±0.06 tolerance, so any two small rates matched (1.8% "equalled" 4.5%), and counts were allowed to be 1% off. **Fix:** counts must match exactly, and rounding is accepted only to the precision the model actually rounded to. Re-scoring every saved run (`evals/rescore.py`, no API calls) flipped two verdicts: Haiku without the dictionary had counted 139 flights that left the gate and were later cancelled as "departed late" (h09). That also revealed the dictionary itself was wrong: it claimed `dep_delay` is NULL for every cancelled flight, but 18,078 have one. The note is corrected.
5. **A tie in the gold answer.** For "top 5 routes by cancellation rate", ranks 4 and 5 tie at 8.29%, so a correct answer could fail on order alone. **Fix:** found by checking every top-N gold answer for ties before running. That question is now scored as a set (rank 6 is a clear 8.02%).


## Data

[BTS Airline On-Time Performance](https://www.transtats.bts.gov/), Jan 2020 – Nov 2025, all reporting carriers: 37,759,151 scheduled flights including cancellations and diversions (my earlier 36.3M-flight delay analysis excluded them).

| Table | Rows | What |
|---|---|---|
| `flights` | 37.8M | one row per scheduled flight, 38 columns |
| `airlines` | 18 | carrier code → name |
| `airports` | 385 | airport code → name, city, state, lat/long |

## Reproduce

- `python evals/run_evals.py --check-gold` validates all gold SQL with no API calls.
- `python evals/run_evals.py --setups claude-haiku-5-5:retrieved claude-haiku-5-5:retrieved:nodict --runs 3 --max-cost 1.00 --questions evals/questions_hard.yaml` runs a benchmark. Setups are `MODEL:MODE[:RETRIES][:nodict]`, and `--max-cost` is a hard spending cap.
- `python evals/rescore.py evals/results/<run>` re-scores a saved run with the current scorer, with no API calls.
