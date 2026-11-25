# FlightQL

**Ask questions about U.S. flights in plain English. Get answers you can check.**

![FlightQL answering "Which 5 airports had the worst average arrival delays in 2024?"](docs/demo.gif)

## What it is

FlightQL is a question-answering tool over **37.8 million U.S. domestic flights** (2020–2025, from the Bureau of Transportation Statistics). You type a question like *"Which airline was most on time in 2024?"* An AI model (Claude) writes a database query to answer it. FlightQL **checks that the query is valid and safe before running it**, runs it, and replies with:

- a plain-English answer,
- the exact SQL that produced it,
- any assumptions it made (for example, what "most on time" was taken to mean).

## Why I built it

"Chat with your data" tools, where an AI writes SQL for non-technical users, are now common. They fail in three ways:

1. **They make things up.** The model references a column that doesn't exist.
2. **They can be unsafe.** A query, or a malicious prompt, tries to delete data or read files.
3. **They're confidently wrong.** The query runs fine but answers the wrong question, because the model misunderstood the data. This is the most dangerous failure, because nothing looks broken.

I wanted to build a version that guards against all three, and then **measure** how much it can be trusted instead of just claiming it works.

This is a follow-up to my [U.S. flight delay analysis](https://github.com/HudsonPifer1217/flight-delay-analysis), a statistical study of what causes delays across 36.3 million flights. That project answered the questions I chose. FlightQL lets anyone ask their own questions of the same BTS data.

## How it works

```
 Question
    │
    ▼
 1. Context    Give Claude the database layout plus a "data dictionary"
    │          that explains what each column really means
    ▼
 2. Generate   Claude returns the SQL and the columns it says it used
    │
    ▼
 3. Validate   Every table and column must exist; only read-only queries pass;
    │          no file or system access.  If it fails, send the error back
    │          to Claude to fix (up to 2 retries)
    ▼
 4. Execute    Run on a locked-down, read-only database with a time limit
    │
    ▼
 Answer + SQL + assumptions
```

Safety comes in two layers: the validator (step 3) rejects bad SQL, and even if something slipped through, the database connection itself can't write or touch files (step 4).

## What I found

I wrote 49 test questions with known correct answers. Fifteen of them were designed to be tricky, and the set includes questions that should be refused and prompt-injection attempts. I ran them against the full database.

- **With the data dictionary, every question was answered correctly** by both Claude Opus and the much smaller Claude Haiku, and every unanswerable or malicious request was refused.
- **The data dictionary mattered most.** For example, the `weather_delay` column is empty unless a flight was late. Without that explanation, the model averaged only the late flights and reported 3.61 minutes, 5× too high. With it, the model got 0.74. Removing the dictionary dropped accuracy to 87% on the hard questions.
- **The small model was enough.** Haiku matched Opus at about 1/30th of the cost ($0.0004 vs $0.0128 per question).
- **Testing caught my own mistakes**, including a grader that was too lenient and an error in my data dictionary. Both were fixed and all results re-scored.

These are 49 hand-written questions, which is a small test. A perfect score shows that these kinds of questions work reliably, not that every possible question will.

Full methodology, tables, and failure write-ups: **[docs/EVALUATION.md](docs/EVALUATION.md)**. Design decisions: [DECISIONS.md](DECISIONS.md).

## Built with

Python · Claude API (structured outputs) · DuckDB · sqlglot (SQL parsing and validation) · FastAPI · Pydantic · Docker · pytest + GitHub Actions (105 tests, no API key needed)

Developed with [Claude Code](https://claude.com/claude-code) as an AI pair programmer, which is why Claude appears as a co-author on commits.

## Run it

```bash
pip install -e ".[dev]"
python scripts/build_db.py      # downloads the flight data (~2 GB, about an hour)
cp .env.example .env            # add your ANTHROPIC_API_KEY
python -m flightql "Which airline had the most flights in 2023?"
```

To use it as a web API, run `uvicorn flightql.api:app`, then open `http://localhost:8000/docs`.

To run the API in Docker instead (after building the database):

```bash
docker build -t flightql .
docker run -p 8000:8000 --env-file .env -v "$(pwd)/data:/app/data" flightql
```

To run the tests, run `pytest`. They use a small fake database and a fake model, so there's no download and no cost.

## Project layout

```
flightql/    the app: schema + data dictionary, prompt, validator, sandboxed DB, API
evals/       test questions, scoring, benchmark runner, saved results
tests/       unit tests
scripts/     builds the database from public BTS data
```
