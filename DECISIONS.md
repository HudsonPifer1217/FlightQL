# Decisions

Running log of design choices and why. Newest at the bottom.

## 2026-11-24: Initial build

**Rebuilt the dataset from BTS instead of reusing the old Parquet.** The 36M-row Parquet from the flight-delay-analysis project was gitignored and no longer on disk. `scripts/build_db.py` re-downloads Jan 2020 – Nov 2025 and is resumable (`_load_log` table), so it's reproducible for anyone cloning the repo.

**Kept cancelled and diverted flights.** The original analysis dropped them (it studied delays). For a question-answering system that makes "cancellation rate" questions unanswerable, so they're kept as boolean flags. Delay columns are NULL for them, and the data dictionary says so.

**Renamed BTS columns to snake_case and wrote a data dictionary.** Names like `CRSDepTime` or `DepDel15` invite wrong guesses. The dictionary tells the model that times are HHMM integers, that delay-cause columns are only populated when `arr_delay >= 15`, and that `day_of_week` 1 = Monday. Cheap, and it removes a whole class of plausible-but-wrong SQL.

**Added `airlines` and `airports` lookup tables.** One wide table would make the "joins" eval category meaningless. The `airports` table is the reference CSV plus every code seen in flights, so every flight joins.

**Retrieval is column-level, not table-level.** With three tables, table-level retrieval does nothing. `flights` has 38 columns, so retrieval picks core columns plus keyword-matched ones. The eval compares this against sending the full schema, since retrieval can also hurt (it can hide a needed column).

**Two guardrail layers.** The sqlglot validator is the first. The DuckDB connection itself is the second: `read_only=True`, `enable_external_access=false`, and `lock_configuration=true` (so a query can't turn access back on). Tests cover both layers independently, so a validator bug alone can't cause a write or a file read.

**Honesty check is asymmetric.** Using a column you didn't declare is an error (it triggers a retry). Declaring a column you didn't use is only a warning. Over-declaring is harmless, and failing on it would burn retries for nothing.

**Retries are stateless re-asks, not a growing conversation.** Each retry sends the question plus the failed SQL and exact error as one new user message. That keeps the system prompt (schema) cacheable, keeps each request small, and avoids replaying thinking blocks between turns.

**Models.** Claude Opus 5.5 is the default, and Claude Haiku 5.5 is the small/fast comparison. Opus requests opt into server-side refusal fallbacks. Pricing in `config.py` is used for per-question cost.

**Eval scoring compares result sets with tolerance.** Exact SQL matching would penalize equivalent queries. Results match when every gold column has a predicted column with the same values (1% / 0.06 tolerance, fraction-or-percent accepted). Top-N questions also compare order. `alt_gold` covers legitimate alternative answers (airline name vs code).

**Ambiguous questions are scored on behavior, not a single right answer.** "Which airports are worst for delays?" has no gold result. It passes if the agent answers *and* states its definition in `assumptions`.

## 2026-11-24: First live runs (dev DB, one month)

**Date coverage now comes from the data.** The first live run on a one-month dev DB answered "across the full Jan 2020 - Nov 2025 period" because that range was hardcoded in the table description. The catalog now reads `MIN/MAX(flight_date)` and the row count at load time and puts them in the prompt. This is the same principle as the schema check: grounding should come from the database, not from documentation that can go stale.

## 2026-11-25: Full database built, gold SQL checked

**Full build:** 71 months (2020-01 to 2025-11), 37,759,151 scheduled flights (36,826,021 completed), 18 airlines, 385 airports. It took about an hour with no download failures. Sanity checks match known patterns: 6.0% cancellations and negative average delay in 2020 (COVID), Southwest the largest carrier.

**Checked every gold query against the real data.** All 27 gold/alt queries pass the validator and run. Then each top-N question was checked for near-ties at the cutoff, since a tie makes the "right" order arbitrary and would penalize correct answers. j01 had ranks 4 and 5 tied at 8.29%, so it is now scored as a set (the top 5 is stable, because rank 6 is 8.02%). The other top-N questions have clear gaps.

## 2026-11-25: First eval run (1 pass, 3 setups): the benchmark is saturated

**Result:** Haiku 5.5 (relevant columns), Opus 5.5 (relevant columns) and Opus 5.5 (full schema) all scored 100% on every metric. All 102 answers passed on the first attempt, so the retry loop never ran. Cost per question: Haiku $0.0004, Opus retrieved $0.0128, Opus full $0.0215 (an upper bound; the total for the run was ≤ $1.18). The flights row count was unchanged afterwards.

**Audit of the perfect score.** The answers were checked by hand (SQL and rows) and are genuinely correct. A cross-check scored every prediction against every *other* question's gold. That produced 9 cross-matches, which were coincidences of single-value answers: "Southwest Airlines" answers both s01 and j06, and a05's airline ranking happens to start with j02's answer (the ordered top-N rule only compares the first k rows). No question passed with a wrong answer.

**Conclusion:** the 34 questions measure that the system works, but they can't tell the setups apart (ceiling effect). They also never triggered the validator or the retries, so this run gives no evidence that either one adds value. The finding that does carry weight: Haiku matched Opus at about 1/30th the cost. Next step: a harder tier built to produce failures.

## 2026-11-25: Hard tier (15 questions) and data-dictionary ablation

**Setup:** 15 questions each built around one trap in the data or wording (HHMM times, crossing midnight, NULL delay-cause columns, a city with two airports, day-of-week numbering, a misspelled airline, window functions, out-of-range dates, a missing aircraft-model column). Haiku ran 3× and Opus 1×, each with and without the data dictionary. Spend was ≤ $0.51.

| Setup | Hard-tier accuracy (13 exact) | Refusals (2) |
|---|---|---|
| Haiku + dictionary (3 runs) | 100% ± 0% | 100% |
| Haiku, no dictionary (3 runs) | 87% ± 4% (h10 failed 3/3, h09 2/3) | 100% |
| Opus + dictionary (1 run) | 100% | 100% |
| Opus, no dictionary (1 run) | 92% as scored; **100% after manual review** | 100% |

**Manual review of every failure.** I read all 5 failing answers.
- h10 (Haiku, no dictionary) is a real error, repeated in all 3 runs. `AVG(weather_delay)` returned 3.61 min instead of 0.74, because without the dictionary note the model didn't know the column is NULL for on-time flights. With the dictionary, the same model wrote `COALESCE(weather_delay, 0)` and explained why. **This is the clearest evidence that the data dictionary changes answers, not just style.**
- h13 (Opus, no dictionary) is a grader false negative. It answered with the `dep_time_block` '2000-2059' rather than hour 20, which is the same answer. Added as an `alt_gold`.
- h06 (Opus, no dictionary) is a grader false negative, and the right number (9.07, both airports) is in the result. It returned a ROLLUP breakdown (all Chicago + MDW + ORD), and the scorer requires one row. Left strict on purpose: the question asked for one number. It's reported here rather than loosening the grader.

**What this says about the system:**
- Across all 222 question-runs (102 standard + 120 hard) so far, no first attempt referenced a nonexistent table or column, and no question needed a retry. On this schema, current Claude models don't hallucinate columns, so the validator and retry loop are a safety net (they're proven by unit tests and by adversarial cases), not an accuracy booster. An honest claim is "guardrails with zero false blocks across 222 real runs", not "retries raised accuracy".
- The accuracy lever that measurably mattered was the data dictionary (semantic grounding), not schema grounding.
- Automatic grading had 2 false negatives in 5 failures, so failures need human review before they're reported.

## Scorer tightened after review (2026-11-25)

The original scorer allowed |a - b| <= max(1%, 0.06) for every number, which made any
two small fractions equal, and compared counts with the same 1% slack. Counts are now
exact; other numbers pass within 1% relative or when they equal gold rounded to the
predicted precision (bounded so rounding can't erase a small rate). All saved runs were
re-scored offline with `evals/rescore.py`. Changes: h09 (Haiku, no dictionary) failed in
2 of 3 runs, because it counted 139 flights cancelled after leaving the gate; h13
(Opus, no dictionary) now passes because its alt_gold was added after that run. The
`dep_time` / `dep_delay` dictionary notes wrongly said "NULL if cancelled" (18,078
cancelled flights have a departure delay) and were corrected.
