"""Validator: hallucination checks, read-only guardrails, honesty check, LIMIT."""
import pytest

from flightql.validate import validate_sql


def ok(sql, catalog, **kw):
    r = validate_sql(sql, catalog, **kw)
    assert r.ok, r.errors
    return r


def bad(sql, catalog, **kw):
    r = validate_sql(sql, catalog, **kw)
    assert not r.ok, f"expected rejection: {sql}"
    return r


# ---------- valid queries ----------

def test_simple_select_passes_and_gets_limit(catalog):
    r = ok("SELECT origin, AVG(arr_delay) AS d FROM flights GROUP BY origin", catalog,
           max_rows=100)
    assert "LIMIT 100" in r.sql
    assert r.columns_used == {"flights.origin", "flights.arr_delay"}
    assert r.tables_used == {"flights"}


def test_smaller_model_limit_is_kept_larger_is_capped(catalog):
    assert "LIMIT 5" in ok("SELECT origin FROM flights LIMIT 5", catalog, max_rows=100).sql
    assert "LIMIT 100" in ok("SELECT origin FROM flights LIMIT 99999", catalog, max_rows=100).sql


def test_join_with_aliases_resolves_real_columns(catalog):
    r = ok("""SELECT a.airline_name, COUNT(*) AS n
              FROM flights f JOIN airlines a ON f.airline_code = a.airline_code
              WHERE f.year = 2024 GROUP BY a.airline_name""", catalog)
    assert {"flights.airline_code", "flights.year", "airlines.airline_name"} <= r.columns_used


def test_cte_names_are_not_treated_as_missing_tables(catalog):
    ok("""WITH by_origin AS (SELECT origin, AVG(arr_delay) AS d FROM flights GROUP BY origin)
          SELECT origin, d FROM by_origin WHERE d > 5 ORDER BY d DESC""", catalog)


def test_union_is_allowed(catalog):
    ok("SELECT origin AS code FROM flights UNION SELECT airport_code FROM airports", catalog)


# ---------- hallucination checks ----------

def test_unknown_column_rejected_with_suggestion(catalog):
    r = bad("SELECT origin, weather FROM flights", catalog)
    assert "weather" in r.errors[0]
    assert "weather_delay" in r.errors[0]  # did-you-mean suggestion


def test_unknown_table_rejected(catalog):
    r = bad("SELECT * FROM weather_reports", catalog)
    assert "does not exist" in r.errors[0]


def test_column_from_wrong_table_rejected(catalog):
    bad("SELECT airline_name FROM flights", catalog)


# ---------- read-only guardrails ----------

@pytest.mark.parametrize("sql", [
    "DROP TABLE flights",
    "DELETE FROM flights",
    "UPDATE flights SET arr_delay = 0",
    "INSERT INTO airlines VALUES ('ZZ', 'Fake')",
    "CREATE TABLE x AS SELECT * FROM flights",
    "ALTER TABLE flights ADD COLUMN x INT",
    "ATTACH 'other.db' AS other",
    "COPY flights TO 'out.csv'",
    "PRAGMA database_list",
    "SET enable_external_access = true",
    "INSTALL httpfs",
])
def test_non_select_statements_rejected(catalog, sql):
    bad(sql, catalog)


@pytest.mark.parametrize("sql", [
    "SELECT origin FROM flights; DROP TABLE flights",
    "SELECT 1; -- harmless\nDELETE FROM flights",
    "SELECT origin FROM flights;; SELECT dest FROM flights",
])
def test_multiple_statements_rejected(catalog, sql):
    r = bad(sql, catalog)
    assert "exactly one" in r.errors[0]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM read_parquet('s3://bucket/x.parquet')",
    "SELECT * FROM query('SELECT 1')",
    "SELECT getenv('HOME')",
    "SELECT * FROM duckdb_tables()",
    "SELECT * FROM glob('*')",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM other_db.main.flights",
])
def test_file_system_and_nested_sql_access_rejected(catalog, sql):
    bad(sql, catalog)


def test_garbage_does_not_parse(catalog):
    r = bad("SELEC origin FRM flights", catalog)
    assert r.errors


# ---------- honesty check ----------

def test_undeclared_column_is_an_error(catalog):
    r = bad("SELECT origin FROM flights WHERE cancelled", catalog,
            declared_columns=["flights.origin"], declared_tables=["flights"])
    assert "flights.cancelled" in r.errors[0]


def test_bare_column_names_are_accepted_as_declarations(catalog):
    ok("SELECT origin FROM flights WHERE cancelled", catalog,
       declared_columns=["origin", "cancelled"], declared_tables=["flights"])


def test_declared_but_unused_is_only_a_warning(catalog):
    r = ok("SELECT origin FROM flights", catalog,
           declared_columns=["flights.origin", "flights.dest"], declared_tables=["flights"])
    assert r.warnings


def test_undeclared_table_is_an_error(catalog):
    bad("SELECT a.airline_name FROM flights f JOIN airlines a USING (airline_code)", catalog,
        declared_columns=["airlines.airline_name", "flights.airline_code",
                          "airlines.airline_code"],
        declared_tables=["flights"])
