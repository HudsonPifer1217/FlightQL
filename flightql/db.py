"""Read-only, sandboxed DuckDB execution.

This is the second guardrail layer. Even if a query got past the validator, the
connection itself cannot write (read_only), cannot touch files or the network
(enable_external_access=false), and cannot change those settings back
(lock_configuration). Long queries are interrupted after a timeout.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import duckdb


class QueryTimeout(Exception):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool


def connect_readonly(db_path: str | Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(
        str(db_path),
        read_only=True,
        config={"enable_external_access": False, "autoinstall_known_extensions": False,
                "autoload_known_extensions": False},
    )
    con.execute("SET lock_configuration = true")
    return con


def run_query(con: duckdb.DuckDBPyConnection, sql: str, max_rows: int,
              timeout_s: float) -> QueryResult:
    """Run one SELECT. Fetches max_rows + 1 to detect truncation."""
    cur = con.cursor()
    timed_out = threading.Event()

    def _interrupt():
        timed_out.set()
        cur.interrupt()

    timer = threading.Timer(timeout_s, _interrupt)
    timer.start()
    try:
        cur.execute(sql)
        rows = cur.fetchmany(max_rows + 1)
        columns = [d[0] for d in cur.description]
    except duckdb.InterruptException as e:
        if timed_out.is_set():
            raise QueryTimeout(f"query exceeded {timeout_s:.0f}s timeout") from e
        raise
    finally:
        timer.cancel()
        cur.close()
    return QueryResult(columns, rows[:max_rows], truncated=len(rows) > max_rows)
