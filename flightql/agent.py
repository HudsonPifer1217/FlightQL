"""The FlightQL pipeline: context -> generate -> validate -> (retry) -> execute -> answer.

Nothing reaches the database until it passes validation. Every step of every
question is written as one JSON line to logs/flightql.jsonl.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel, Field

from .config import Settings
from .db import QueryTimeout, connect_readonly, run_query
from .generate import LLMClient, build_user_message
from .retrieve import build_context
from .schema import Catalog, load_catalog
from .validate import validate_sql

Status = Literal["answered", "refused", "failed"]


class Attempt(BaseModel):
    n: int
    sql: str | None = None
    answerable: bool | None = None
    tables_used: list[str] = Field(default_factory=list)
    columns_used: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    hallucinated_schema: bool = False   # referenced a table/column that doesn't exist
    execution_error: str | None = None
    llm_error: str | None = None
    raw_output: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


class AgentResult(BaseModel):
    question: str
    status: Status
    answer: str
    sql: str | None = None
    assumptions: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[list] = Field(default_factory=list)
    truncated: bool = False
    attempts: list[Attempt] = Field(default_factory=list)
    schema_shown: dict[str, list[str]] = Field(default_factory=dict)
    model: str = ""
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


def _is_hallucination(errors: list[str]) -> bool:
    return any("does not exist" in e or "could not be resolved" in e for e in errors)


def _jsonable(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, float) and v != v:  # NaN
        return None
    if hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
        return float(v)  # Decimal
    return v


class FlightQLAgent:
    def __init__(self, llm: LLMClient, settings: Settings | None = None,
                 con: duckdb.DuckDBPyConnection | None = None):
        self.llm = llm
        self.settings = settings or Settings()
        self.con = con or connect_readonly(self.settings.db_path)
        self.catalog: Catalog = load_catalog(self.con)
        self.settings.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path: Path = self.settings.log_dir / "flightql.jsonl"

    def ask(self, question: str) -> AgentResult:
        s = self.settings
        t0 = time.perf_counter()
        schema_text, shown = build_context(question, self.catalog, s.schema_mode,
                                           docs=s.data_dictionary)
        attempts: list[Attempt] = []
        result: AgentResult | None = None
        prev_sql, prev_error = None, None
        spent = {"in": 0, "out": 0, "cost": 0.0}

        def charge(call):
            spent["in"] += call.input_tokens
            spent["out"] += call.output_tokens
            spent["cost"] += call.cost_usd

        for n in range(1, s.max_retries + 2):
            call = self.llm.generate_sql(schema_text, build_user_message(question, prev_sql,
                                                                         prev_error))
            charge(call)
            a = Attempt(n=n, raw_output=call.raw_text, input_tokens=call.input_tokens,
                        output_tokens=call.output_tokens, latency_s=round(call.latency_s, 3))
            attempts.append(a)

            if call.refused:
                result = self._refusal(question, "The model declined to answer this request.")
                break
            if call.output is None:
                a.llm_error = call.error or "no output"
                prev_sql, prev_error = None, None  # nothing to correct; just try again
                continue

            out = call.output
            a.answerable, a.sql = out.answerable, out.sql or None
            a.tables_used, a.columns_used = out.tables_used, out.columns_used
            if not out.answerable or not out.sql.strip():
                result = self._refusal(question, out.explanation or "This question can't be "
                                       "answered from the flight data.")
                break

            v = validate_sql(out.sql, self.catalog, s.max_rows,
                             declared_columns=out.columns_used, declared_tables=out.tables_used)
            a.validation_errors, a.validation_warnings = v.errors, v.warnings
            a.hallucinated_schema = _is_hallucination(v.errors)
            if not v.ok:
                prev_sql, prev_error = out.sql, "Validation failed:\n" + v.error_text()
                continue

            try:
                qr = run_query(self.con, v.sql, s.max_rows, s.query_timeout_s)
            except QueryTimeout as e:
                a.execution_error = str(e)
                prev_sql, prev_error = out.sql, f"{e}. Simplify the query or aggregate more."
                continue
            except duckdb.Error as e:
                a.execution_error = f"{type(e).__name__}: {e}"
                prev_sql, prev_error = out.sql, f"DuckDB error: {e}"
                continue

            answer = out.explanation
            if s.summarize:
                summ = self.llm.summarize(question, v.sql, qr.columns, qr.rows, qr.truncated)
                charge(summ)
                if summ.raw_text:
                    answer = summ.raw_text
            result = AgentResult(
                question=question, status="answered", answer=answer, sql=v.sql,
                assumptions=out.assumptions, columns=qr.columns,
                rows=[[_jsonable(x) for x in r] for r in qr.rows], truncated=qr.truncated,
            )
            break

        if result is None:
            last = attempts[-1]
            why = (last.validation_errors or [last.execution_error or last.llm_error or ""])[0]
            result = AgentResult(
                question=question, status="failed",
                answer=f"I couldn't produce a valid query for this question after "
                       f"{len(attempts)} attempts. Last problem: {why}",
            )

        result.attempts = attempts
        result.schema_shown = shown
        result.model = self.llm.model
        result.latency_s = round(time.perf_counter() - t0, 3)
        result.input_tokens, result.output_tokens = spent["in"], spent["out"]
        result.cost_usd = round(spent["cost"], 6)
        self._log(result)
        return result

    @staticmethod
    def _refusal(question: str, reason: str) -> AgentResult:
        return AgentResult(question=question, status="refused", answer=reason)

    def _log(self, r: AgentResult) -> None:
        record = {
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
            "schema_mode": self.settings.schema_mode,
            **r.model_dump(exclude={"rows"}),
            "n_rows": len(r.rows),
        }
        with open(self.log_path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
