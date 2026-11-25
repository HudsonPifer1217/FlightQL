"""Second guardrail layer: the connection itself is read-only and sandboxed,
so even SQL that skipped the validator cannot write or read files."""
import duckdb
import pytest

from flightql.db import QueryTimeout, run_query


def test_connection_cannot_write(con):
    with pytest.raises(duckdb.Error):
        con.execute("DELETE FROM flights")
    with pytest.raises(duckdb.Error):
        con.execute("CREATE TABLE t (x INT)")


def test_connection_cannot_read_files(con, tmp_path):
    f = tmp_path / "secret.csv"
    f.write_text("a\n1\n")
    with pytest.raises(duckdb.Error):
        con.execute(f"SELECT * FROM read_csv('{f}')").fetchall()


def test_sandbox_settings_are_locked(con):
    with pytest.raises(duckdb.Error):
        con.execute("SET enable_external_access = true")


def test_row_cap_and_truncation_flag(con):
    r = run_query(con, "SELECT * FROM flights", max_rows=10, timeout_s=5)
    assert len(r.rows) == 10 and r.truncated
    r = run_query(con, "SELECT COUNT(*) FROM flights", max_rows=10, timeout_s=5)
    assert not r.truncated


def test_timeout_interrupts_long_query(con):
    slow = ("SELECT COUNT(*) FROM range(100000000) a, range(100000) b "
            "WHERE (a.range * b.range) % 7 = 3")
    with pytest.raises(QueryTimeout):
        run_query(con, slow, max_rows=10, timeout_s=0.5)
