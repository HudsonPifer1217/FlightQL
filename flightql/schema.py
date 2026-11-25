"""Schema catalog: the real tables and columns, plus a data dictionary.

The catalog is the source of truth for both the prompt (what the model is told
exists) and the validator (what the SQL is checked against). It is loaded from
DuckDB's information_schema, so it can never drift from the actual database.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import duckdb

# Human-written descriptions. BTS column semantics are not obvious from names
# (e.g. times are HHMM integers, delay-cause minutes only exist for late flights),
# and these notes are what keep the model from guessing.
TABLE_DOCS = {
    "flights": "One row per scheduled domestic U.S. flight (BTS On-Time Performance). "
               "Includes cancelled and diverted flights.",
    "airlines": "Lookup of carrier codes to airline names. Join on airline_code.",
    "airports": "Lookup of airport codes to names and locations. Join flights.origin "
                "or flights.dest to airports.airport_code.",
}

COLUMN_DOCS = {
    "flights": {
        "flight_date": "Date of the flight (local).",
        "year": "Calendar year of the flight.",
        "quarter": "Quarter 1-4.",
        "month": "Month 1-12.",
        "day_of_month": "Day of month 1-31.",
        "day_of_week": "1=Monday ... 7=Sunday.",
        "airline_code": "Two-character carrier code, e.g. 'AA', 'WN'. Join to airlines.",
        "flight_number": "Carrier flight number.",
        "tail_number": "Aircraft tail number.",
        "origin": "Origin airport code, e.g. 'SFO'.",
        "origin_city": "Origin city as 'City, ST'.",
        "origin_state": "Origin state, two-letter code.",
        "dest": "Destination airport code.",
        "dest_city": "Destination city as 'City, ST'.",
        "dest_state": "Destination state, two-letter code.",
        "sched_dep_time": "Scheduled departure, local time as HHMM integer (e.g. 1730 = 5:30 PM). "
                          "Hour = sched_dep_time // 100.",
        "dep_time": "Actual departure, local HHMM integer. NULL if cancelled.",
        "dep_delay": "Departure delay in minutes; negative = left early. NULL if cancelled.",
        "dep_delayed_15": "True if departure delay >= 15 minutes.",
        "dep_time_block": "Scheduled departure hour block, e.g. '0600-0659'.",
        "taxi_out": "Taxi-out minutes (gate to wheels-off). Proxy for airport congestion.",
        "taxi_in": "Taxi-in minutes (wheels-on to gate).",
        "sched_arr_time": "Scheduled arrival, local HHMM integer.",
        "arr_time": "Actual arrival, local HHMM integer.",
        "arr_delay": "Arrival delay in minutes; negative = early. NULL if cancelled or diverted. "
                     "This is the standard 'delay' measure.",
        "arr_delayed_15": "True if arrival delay >= 15 minutes (the official 'late' definition).",
        "cancelled": "True if the flight was cancelled.",
        "cancellation_code": "Reason if cancelled: 'A'=carrier, 'B'=weather, 'C'=national air "
                             "system, 'D'=security. NULL otherwise.",
        "diverted": "True if the flight was diverted.",
        "sched_elapsed_time": "Scheduled gate-to-gate minutes.",
        "actual_elapsed_time": "Actual gate-to-gate minutes.",
        "air_time": "Minutes in the air.",
        "distance": "Route distance in miles.",
        "carrier_delay": "Minutes of arrival delay attributed to the carrier. "
                         "Only filled when arr_delay >= 15; otherwise NULL.",
        "weather_delay": "Minutes of arrival delay attributed to significant weather. "
                         "Only filled when arr_delay >= 15; otherwise NULL.",
        "nas_delay": "Minutes attributed to the national air system (traffic, ATC). "
                     "Only filled when arr_delay >= 15.",
        "security_delay": "Minutes attributed to security. Only filled when arr_delay >= 15.",
        "late_aircraft_delay": "Minutes caused by the aircraft arriving late from its previous "
                               "flight. Only filled when arr_delay >= 15.",
    },
    "airlines": {
        "airline_code": "Two-character carrier code.",
        "airline_name": "Airline name, e.g. 'Delta Air Lines'.",
    },
    "airports": {
        "airport_code": "Three-letter airport code.",
        "airport_name": "Airport name, e.g. 'San Francisco International'.",
        "city": "City name.",
        "state": "Two-letter state code.",
        "latitude": "Latitude (may be NULL for small airports).",
        "longitude": "Longitude (may be NULL for small airports).",
    },
}

# Low-cardinality columns whose allowed values are worth showing to the model.
ENUM_COLUMNS = {("airlines", "airline_code"), ("flights", "cancellation_code")}


@dataclass
class Column:
    name: str
    type: str
    description: str = ""


@dataclass
class Table:
    name: str
    columns: list[Column]
    description: str = ""
    sample_rows: list[tuple] = field(default_factory=list)
    value_hints: dict[str, list] = field(default_factory=dict)

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name == name), None)


@dataclass
class Catalog:
    tables: dict[str, Table]

    def as_sqlglot_schema(self) -> dict[str, dict[str, str]]:
        return {t.name: {c.name: c.type for c in t.columns} for t in self.tables.values()}

    def render(self, columns: dict[str, list[str]] | None = None, samples: bool = True,
               docs: bool = True) -> str:
        """Render tables (optionally only a subset of columns) as prompt text.

        docs=False drops the hand-written column notes and value lists (the data
        dictionary), leaving names, types and sample rows. Used for the ablation.
        """
        out = []
        for t in self.tables.values():
            if columns is not None and t.name not in columns:
                continue
            keep = columns[t.name] if columns is not None else [c.name for c in t.columns]
            cols = [c for c in t.columns if c.name in keep]
            out.append(f"TABLE {t.name} -- {t.description}")
            for c in cols:
                line = f"  {c.name} {c.type}"
                if docs and c.description:
                    line += f" -- {c.description}"
                if docs and c.name in t.value_hints:
                    line += f" Values: {', '.join(map(str, t.value_hints[c.name]))}."
                out.append(line)
            if samples and t.sample_rows:
                idx = [i for i, c in enumerate(t.columns) if c.name in keep]
                out.append(f"  sample rows ({', '.join(c.name for c in cols)}):")
                for row in t.sample_rows:
                    out.append("    " + repr(tuple(row[i] for i in idx)))
            out.append("")
        return "\n".join(out).strip()


def load_catalog(con: duckdb.DuckDBPyConnection, n_samples: int = 3) -> Catalog:
    rows = con.execute("""
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'main' AND table_name NOT LIKE '\\_%' ESCAPE '\\'
        ORDER BY table_name, ordinal_position
    """).fetchall()
    tables: dict[str, Table] = {}
    for tname, cname, ctype in rows:
        t = tables.setdefault(tname, Table(tname, [], TABLE_DOCS.get(tname, "")))
        t.columns.append(Column(cname, ctype, COLUMN_DOCS.get(tname, {}).get(cname, "")))

    # Coverage comes from the data, not from docs, so the model never claims a
    # date range the database doesn't have.
    if "flights" in tables and tables["flights"].column("flight_date"):
        lo, hi, n = con.execute(
            "SELECT MIN(flight_date), MAX(flight_date), COUNT(*) FROM flights").fetchone()
        if lo is not None:
            tables["flights"].description += (
                f" Covers {lo:%Y-%m-%d} to {hi:%Y-%m-%d} ({n:,} rows); questions about "
                "dates outside this range cannot be answered.")

    for t in tables.values():
        where ="WHERE NOT cancelled AND carrier_delay IS NOT NULL" if t.name == "flights" else ""
        t.sample_rows = con.execute(f'SELECT * FROM "{t.name}" {where} LIMIT {n_samples}').fetchall()
        if not t.sample_rows and where:
            t.sample_rows = con.execute(f'SELECT * FROM "{t.name}" LIMIT {n_samples}').fetchall()
        for tn, cn in ENUM_COLUMNS:
            if tn == t.name and t.column(cn):
                vals = con.execute(
                    f'SELECT DISTINCT "{cn}" FROM "{tn}" WHERE "{cn}" IS NOT NULL ORDER BY 1'
                ).fetchall()
                t.value_hints[cn] = [v[0] for v in vals]
    return Catalog(tables)
