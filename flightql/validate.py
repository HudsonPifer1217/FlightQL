"""Check model-written SQL against the real schema before it runs.

Rejects SQL that:
  * is not exactly one read-only query (SELECT / UNION / WITH ... SELECT)
  * references a table or column that does not exist (hallucination check)
  * calls a table function or system function that can reach files, settings,
    or run nested SQL (read_csv, query, getenv, ...)
  * uses columns it did not declare in `columns_used` (honesty check)

On success it returns the SQL rewritten with an enforced LIMIT.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from .schema import Catalog

READ_ONLY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

# Statement / clause types that must never appear anywhere in the tree.
FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.Command, exp.Copy, exp.Attach, exp.Detach, exp.Pragma, exp.Set, exp.Use,
    exp.Into, exp.Transaction, exp.Commit, exp.Rollback,
)

# Function-name prefixes/names that can read files, inspect the system, or run SQL.
FORBIDDEN_FUNCS = (
    "read_", "glob", "getenv", "query", "query_table", "sniff_csv", "parquet_",
    "duckdb_", "pragma_", "current_setting", "load_", "install_", "sqlite_", "postgres_",
    "mysql_", "iceberg_", "delta_scan", "http", "s3", "json_execute", "which_secret",
)


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sql: str | None = None                       # normalized SQL with LIMIT enforced
    tables_used: set[str] = field(default_factory=set)
    columns_used: set[str] = field(default_factory=set)  # "table.column"

    def error_text(self) -> str:
        return "\n".join(f"- {e}" for e in self.errors)


def _func_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.sql_name().lower() if isinstance(node, exp.Func) else ""


def _suggest(name: str, options: list[str]) -> str:
    close = difflib.get_close_matches(name, options, n=3, cutoff=0.5)
    return f" Did you mean: {', '.join(close)}?" if close else ""


def _normalize_declared(cols: list[str], tables: set[str], catalog: Catalog) -> set[str]:
    """Turn declared names ('arr_delay' or 'flights.arr_delay') into 'table.column'."""
    out = set()
    for c in cols:
        c = c.strip().strip('"').lower()
        if "." in c:
            out.add(c)
            continue
        owners = [t for t in tables if catalog.tables[t].column(c)]
        out.update(f"{t}.{c}" for t in (owners or ["?"]))
    return out


def validate_sql(sql: str, catalog: Catalog, max_rows: int = 1000,
                 declared_columns: list[str] | None = None,
                 declared_tables: list[str] | None = None) -> ValidationResult:
    res = ValidationResult(ok=False)

    # 1. Parse: exactly one statement.
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except ParseError as e:
        res.errors.append(f"SQL does not parse: {str(e).splitlines()[0]}")
        return res
    if len(statements) != 1:
        res.errors.append(f"Expected exactly one SQL statement, got {len(statements)}. "
                          "Return a single SELECT query.")
        return res
    tree = statements[0]

    # 2. Read-only guardrail.
    if not isinstance(tree, READ_ONLY_ROOTS):
        res.errors.append(f"Only SELECT queries are allowed; got {type(tree).__name__.upper()}.")
        return res
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            res.errors.append(f"Forbidden operation in query: {type(node).__name__.upper()}.")
            return res
        name = _func_name(node)
        if name and any(name.startswith(p) for p in FORBIDDEN_FUNCS):
            res.errors.append(f"Function '{name}' is not allowed (no file, system, or "
                              "nested-SQL access).")
            return res

    # 3. Tables must be real (or CTEs defined in this query).
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    real_tables = list(catalog.tables)
    for t in tree.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):
            res.errors.append(f"Table functions are not allowed: {t.this.sql('duckdb')[:60]}")
            continue
        if t.catalog or (t.db and t.db.lower() != "main"):
            res.errors.append(f"Only tables in the flights database may be used; got "
                              f"'{t.sql('duckdb')}'.")
            continue
        name = t.name.lower()
        if name in cte_names:
            continue
        if name not in catalog.tables:
            res.errors.append(f"Table '{t.name}' does not exist.{_suggest(name, real_tables)} "
                              f"Available tables: {', '.join(real_tables)}.")
            continue
        res.tables_used.add(name)
    if res.errors:
        return res

    # 4. Columns must be real: resolve every column against the schema.
    try:
        qualified = qualify(tree.copy(), schema=catalog.as_sqlglot_schema(), dialect="duckdb",
                            validate_qualify_columns=True, quote_identifiers=False)
    except OptimizeError as e:
        msg = str(e).split(". Line")[0]
        bad = msg.split("'")[1] if "'" in msg else ""
        options = [c.name for t in res.tables_used for c in catalog.tables[t].columns]
        res.errors.append(f"{msg}.{_suggest(bad, options)} "
                          f"Columns must come from the provided schema.")
        return res

    for scope in traverse_scope(qualified):
        for col in scope.columns:
            src = scope.sources.get(col.table)
            if isinstance(src, exp.Table) and src.name.lower() in catalog.tables:
                res.columns_used.add(f"{src.name.lower()}.{col.name.lower()}")

    # 5. Honesty check: what the SQL touches must match what the model declared.
    if declared_columns is not None:
        declared = _normalize_declared(declared_columns, res.tables_used, catalog)
        undeclared = sorted(res.columns_used - declared)
        if undeclared:
            res.errors.append(
                f"columns_used is incomplete: the SQL also uses {', '.join(undeclared)}. "
                "List every column the query reads."
            )
        unused = sorted(d for d in declared - res.columns_used if not d.startswith("?"))
        if unused:
            res.warnings.append(f"Declared but unused columns: {', '.join(unused)}")
    if declared_tables is not None:
        missing = sorted(res.tables_used - {t.lower() for t in declared_tables})
        if missing:
            res.errors.append(f"tables_used is incomplete: the SQL also uses {', '.join(missing)}.")
    if res.errors:
        return res

    # 6. Enforce a row limit (keep a smaller limit the model chose).
    limit = tree.args.get("limit")
    current = None
    if limit is not None and isinstance(limit.expression, exp.Literal):
        try:
            current = int(limit.expression.this)
        except ValueError:
            current = None
    if current is None or current > max_rows:
        tree = tree.limit(max_rows, copy=True)

    res.sql = tree.sql(dialect="duckdb", pretty=True)
    res.ok = True
    return res
