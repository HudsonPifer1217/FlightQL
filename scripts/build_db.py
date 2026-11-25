"""Build data/flights.duckdb from BTS On-Time Performance monthly files.

Downloads each month (Jan 2020 - Nov 2025 by default), loads a curated set of
columns into a `flights` table with readable snake_case names, then builds the
`airlines` and `airports` dimension tables. Resumable: months already loaded
are recorded in `_load_log` and skipped on re-run.

Usage:
    python scripts/build_db.py                                  # full build
    python scripts/build_db.py --start 2024-01 --end 2024-03    # a few months
    python scripts/build_db.py --keep-zips                      # keep raw downloads
"""
from __future__ import annotations

import argparse
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "flights.duckdb"
AIRPORTS_CSV = ROOT / "data" / "reference" / "airports.csv"

URL = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{y}_{m}.zip"
)

# BTS column -> FlightQL column. Same fields as the original
# flight-delay-analysis pipeline, plus cancellation/diversion flags so
# cancellation questions are answerable.
COLUMNS = {
    "FlightDate": "flight_date",
    "Year": "year",
    "Quarter": "quarter",
    "Month": "month",
    "DayofMonth": "day_of_month",
    "DayOfWeek": "day_of_week",
    "Reporting_Airline": "airline_code",
    "Flight_Number_Reporting_Airline": "flight_number",
    "Tail_Number": "tail_number",
    "Origin": "origin",
    "OriginCityName": "origin_city",
    "OriginState": "origin_state",
    "Dest": "dest",
    "DestCityName": "dest_city",
    "DestState": "dest_state",
    "CRSDepTime": "sched_dep_time",
    "DepTime": "dep_time",
    "DepDelay": "dep_delay",
    "DepDel15": "dep_delayed_15",
    "DepTimeBlk": "dep_time_block",
    "TaxiOut": "taxi_out",
    "TaxiIn": "taxi_in",
    "CRSArrTime": "sched_arr_time",
    "ArrTime": "arr_time",
    "ArrDelay": "arr_delay",
    "ArrDel15": "arr_delayed_15",
    "Cancelled": "cancelled",
    "CancellationCode": "cancellation_code",
    "Diverted": "diverted",
    "CRSElapsedTime": "sched_elapsed_time",
    "ActualElapsedTime": "actual_elapsed_time",
    "AirTime": "air_time",
    "Distance": "distance",
    "CarrierDelay": "carrier_delay",
    "WeatherDelay": "weather_delay",
    "NASDelay": "nas_delay",
    "SecurityDelay": "security_delay",
    "LateAircraftDelay": "late_aircraft_delay",
}

INT_TYPES = {
    "year": "SMALLINT", "quarter": "TINYINT", "month": "TINYINT",
    "day_of_month": "TINYINT", "day_of_week": "TINYINT",
    "flight_number": "INTEGER",
    "sched_dep_time": "SMALLINT", "dep_time": "SMALLINT",
    "sched_arr_time": "SMALLINT", "arr_time": "SMALLINT",
}
BOOL_COLS = {"dep_delayed_15", "arr_delayed_15", "cancelled", "diverted"}
DOUBLE_COLS = {
    "dep_delay", "arr_delay", "taxi_out", "taxi_in", "sched_elapsed_time",
    "actual_elapsed_time", "air_time", "distance", "carrier_delay",
    "weather_delay", "nas_delay", "security_delay", "late_aircraft_delay",
}

# Carriers reporting to BTS 2020-2025.
AIRLINES = {
    "9E": "Endeavor Air", "AA": "American Airlines", "AS": "Alaska Airlines",
    "B6": "JetBlue Airways", "DL": "Delta Air Lines", "EV": "ExpressJet Airlines",
    "F9": "Frontier Airlines", "G4": "Allegiant Air", "HA": "Hawaiian Airlines",
    "MQ": "Envoy Air", "NK": "Spirit Airlines", "OH": "PSA Airlines",
    "OO": "SkyWest Airlines", "QX": "Horizon Air", "UA": "United Airlines",
    "WN": "Southwest Airlines", "YV": "Mesa Airlines", "YX": "Republic Airways",
    "ZW": "Air Wisconsin",
}


def col_type(dst: str) -> str:
    if dst == "flight_date":
        return "DATE"
    if dst in INT_TYPES:
        return INT_TYPES[dst]
    if dst in BOOL_COLS:
        return "BOOLEAN"
    if dst in DOUBLE_COLS:
        return "DOUBLE"
    return "VARCHAR"


def select_expr(src: str, dst: str) -> str:
    col = f"NULLIF(\"{src}\", '')"  # read as all_varchar; empty string -> NULL
    t = col_type(dst)
    if t == "BOOLEAN":
        return f"TRY_CAST({col} AS DOUBLE) = 1 AS {dst}"
    if t == "VARCHAR":
        return f"{col} AS {dst}"
    return f"TRY_CAST({col} AS {t}) AS {dst}"


SELECT_LIST = ",\n    ".join(select_expr(s, d) for s, d in COLUMNS.items())


def months(start: str, end: str) -> list[tuple[int, int]]:
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def download(y: int, m: int, retries: int = 5) -> Path:
    dest = RAW_DIR / f"{y}_{m:02d}.zip"
    if dest.exists() and zipfile.is_zipfile(dest):
        return dest
    tmp = dest.with_suffix(".part")
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(URL.format(y=y, m=m), timeout=120) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            if not zipfile.is_zipfile(tmp):
                raise IOError("downloaded file is not a zip")
            tmp.rename(dest)
            return dest
        except Exception as e:  # BTS is flaky; back off and retry
            print(f"  {y}-{m:02d} attempt {attempt} failed: {e}", flush=True)
            tmp.unlink(missing_ok=True)
            time.sleep(10 * attempt)
    raise RuntimeError(f"could not download {y}-{m:02d}")


def load_month(con: duckdb.DuckDBPyConnection, y: int, m: int, zpath: Path) -> int:
    with zipfile.ZipFile(zpath) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        csv_path = z.extract(name, RAW_DIR / "tmp")
    try:
        con.execute("BEGIN")
        con.execute(f"""
            INSERT INTO flights
            SELECT {SELECT_LIST}
            FROM read_csv('{csv_path}', header=true, all_varchar=true)
        """)
        n = con.execute(
            "SELECT count(*) FROM flights WHERE year = ? AND month = ?", [y, m]
        ).fetchone()[0]
        con.execute("INSERT INTO _load_log VALUES (?, ?, ?, now())", [y, m, n])
        con.execute("COMMIT")
        return n
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        Path(csv_path).unlink(missing_ok=True)


def create_tables(con: duckdb.DuckDBPyConnection) -> None:
    cols = ", ".join(f"{d} {col_type(d)}" for d in COLUMNS.values())
    con.execute(f"CREATE TABLE IF NOT EXISTS flights ({cols})")
    con.execute("""CREATE TABLE IF NOT EXISTS _load_log
        (year INTEGER, month INTEGER, row_count BIGINT, loaded_at TIMESTAMP)""")


def build_dimensions(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE OR REPLACE TABLE airlines (airline_code VARCHAR, airline_name VARCHAR)")
    codes = [r[0] for r in con.execute("SELECT DISTINCT airline_code FROM flights").fetchall()]
    con.executemany(
        "INSERT INTO airlines VALUES (?, ?)", [(c, AIRLINES.get(c, c)) for c in sorted(codes)]
    )
    # The reference CSV covers major airports; fill the rest from the flight
    # records so every code in `flights` joins to `airports`.
    con.execute(f"""
        CREATE OR REPLACE TABLE airports AS
        WITH ref AS (
            SELECT IATA AS code, AIRPORT AS airport_name, CITY AS city, STATE AS state,
                   TRY_CAST(LATITUDE AS DOUBLE) AS latitude,
                   TRY_CAST(LONGITUDE AS DOUBLE) AS longitude
            FROM read_csv('{AIRPORTS_CSV}', header=true, all_varchar=true)
        ),
        seen AS (
            SELECT code, any_value(city_name) AS city_name, any_value(st) AS st FROM (
                SELECT origin AS code, origin_city AS city_name, origin_state AS st FROM flights
                UNION ALL
                SELECT dest, dest_city, dest_state FROM flights
            ) GROUP BY code
        )
        SELECT s.code AS airport_code,
               coalesce(r.airport_name, s.city_name) AS airport_name,
               coalesce(r.city, split_part(s.city_name, ',', 1)) AS city,
               coalesce(r.state, s.st) AS state,
               r.latitude, r.longitude
        FROM seen s LEFT JOIN ref r ON r.code = s.code
        ORDER BY airport_code
    """)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-01")
    ap.add_argument("--end", default="2025-11")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--keep-zips", action="store_true")
    ap.add_argument("--db", type=Path, default=DB_PATH,
                    help="output database (e.g. a small dev DB of a few months)")
    args = ap.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(args.db))
    create_tables(con)
    done = set(con.execute("SELECT year, month FROM _load_log").fetchall())
    todo = [ym for ym in months(args.start, args.end) if ym not in done]
    print(f"{len(done)} months already loaded, {len(todo)} to go", flush=True)

    # Downloads run in parallel; loads happen one at a time on this thread.
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download, y, m): (y, m) for y, m in todo}
        for fut in as_completed(futures):
            y, m = futures[fut]
            zpath = fut.result()
            n = load_month(con, y, m, zpath)
            print(f"loaded {y}-{m:02d}: {n:,} rows", flush=True)
            if not args.keep_zips:
                zpath.unlink(missing_ok=True)

    build_dimensions(con)
    total = con.execute("SELECT count(*) FROM flights").fetchone()[0]
    n_ap = con.execute("SELECT count(*) FROM airports").fetchone()[0]
    n_al = con.execute("SELECT count(*) FROM airlines").fetchone()[0]
    print(f"done: {total:,} flights, {n_ap} airports, {n_al} airlines", flush=True)
    con.close()


if __name__ == "__main__":
    main()
