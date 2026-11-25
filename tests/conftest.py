"""Shared fixtures: a small synthetic flights database and a scripted fake LLM.

The fixture DB is built with the same `create_tables` / `build_dimensions`
functions as the real build script, so tests run against the real schema
without needing the 40M-row database or an API key.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import random
from pathlib import Path

import duckdb
import pytest

from flightql.config import Settings
from flightql.db import connect_readonly
from flightql.generate import LLMCall, SQLResponse
from flightql.schema import load_catalog

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build_db", ROOT / "scripts" / "build_db.py")
build_db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_db)

AIRLINES = ["AA", "DL", "WN", "UA"]
AIRPORTS = [("SFO", "San Francisco, CA", "CA"), ("LAX", "Los Angeles, CA", "CA"),
            ("JFK", "New York, NY", "NY"), ("ORD", "Chicago, IL", "IL")]


def _rows(n: int = 2000, seed: int = 7):
    rng = random.Random(seed)
    for i in range(n):
        d = dt.date(2023, 1, 1) + dt.timedelta(days=rng.randrange(730))
        o, d_ = rng.sample(AIRPORTS, 2)
        cancelled = rng.random() < 0.03
        dep_sched = rng.choice([600, 815, 1230, 1745, 2105])
        arr_delay = None if cancelled else round(rng.gauss(5, 25), 1)
        late = arr_delay is not None and arr_delay >= 15
        yield (
            d, d.year, (d.month - 1) // 3 + 1, d.month, d.day, d.isoweekday(),
            rng.choice(AIRLINES), 1000 + i % 500, f"N{100 + i % 50}XX",
            o[0], o[1], o[2], d_[0], d_[1], d_[2],
            dep_sched, None if cancelled else dep_sched + 5,
            None if cancelled else (arr_delay or 0) - 3, (not cancelled) and late,
            f"{dep_sched // 100:02d}00-{dep_sched // 100:02d}59",
            None if cancelled else rng.uniform(8, 30), None if cancelled else rng.uniform(3, 12),
            dep_sched + 300, None if cancelled else dep_sched + 310,
            arr_delay, (not cancelled) and late,
            cancelled, rng.choice("ABCD") if cancelled else None, False,
            300.0, None if cancelled else 305.0, None if cancelled else 270.0,
            rng.choice([337.0, 2475.0, 740.0]),
            *( [arr_delay / 5] * 5 if late else [None] * 5 ),
        )


@pytest.fixture(scope="session")
def db_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("db") / "test_flights.duckdb"
    con = duckdb.connect(str(path))
    build_db.create_tables(con)
    placeholders = ", ".join("?" * len(build_db.COLUMNS))
    con.executemany(f"INSERT INTO flights VALUES ({placeholders})", list(_rows()))
    build_db.build_dimensions(con)
    con.close()
    return path


@pytest.fixture()
def con(db_path):
    c = connect_readonly(db_path)
    yield c
    c.close()


@pytest.fixture()
def catalog(con):
    return load_catalog(con)


@pytest.fixture()
def settings(db_path, tmp_path) -> Settings:
    return Settings(db_path=db_path, model="fake-model", summarize=False,
                    log_dir=tmp_path / "logs", query_timeout_s=5)


def resp(sql: str, columns: list[str], tables: list[str] | None = None,
         answerable: bool = True, explanation: str = "test", assumptions: str = "") -> SQLResponse:
    return SQLResponse(answerable=answerable, sql=sql, columns_used=columns,
                       tables_used=tables if tables is not None else ["flights"],
                       assumptions=assumptions, explanation=explanation)


class FakeLLM:
    """Returns scripted outputs in order and records every prompt it was sent."""

    def __init__(self, outputs: list[SQLResponse | LLMCall]):
        self.model = "fake-model"
        self.outputs = list(outputs)
        self.messages: list[str] = []

    def generate_sql(self, schema_text: str, user_message: str) -> LLMCall:
        self.messages.append(user_message)
        nxt = self.outputs.pop(0)
        if isinstance(nxt, LLMCall):
            return nxt
        return LLMCall(nxt, nxt.model_dump_json(), self.model, 100, 50, 0.01)

    def summarize(self, question, sql, columns, rows, truncated) -> LLMCall:
        return LLMCall(None, f"summary of {len(rows)} rows", self.model, 10, 10, 0.01)
