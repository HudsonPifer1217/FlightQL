"""Pick the schema context relevant to a question.

`flights` has 38 columns; most questions need a handful. Retrieval keeps a core
set of columns that almost every question uses, adds columns whose name or
keywords match the question, and pulls in a lookup table only when the
question is about names/places. The "full" mode sends everything, which is
the baseline this is compared against in the evals.
"""
from __future__ import annotations

import re

from .schema import Catalog

CORE = {
    "flights": ["flight_date", "year", "month", "airline_code", "origin", "dest",
                "cancelled", "diverted", "arr_delay", "dep_delay"],
}

# Extra trigger words per column (column names and description words also match).
KEYWORDS = {
    ("flights", "day_of_week"): ["weekday", "weekend", "monday", "tuesday", "wednesday",
                                 "thursday", "friday", "saturday", "sunday", "day of week"],
    ("flights", "quarter"): ["quarter", "q1", "q2", "q3", "q4"],
    ("flights", "day_of_month"): ["day of month", "christmas", "holiday", "thanksgiving"],
    ("flights", "sched_dep_time"): ["hour", "time of day", "morning", "evening", "night",
                                    "afternoon", "red-eye", "redeye", "early", "late"],
    ("flights", "dep_time_block"): ["hour", "time of day", "morning", "evening", "block"],
    ("flights", "cancellation_code"): ["cancel", "reason", "why"],
    ("flights", "taxi_out"): ["taxi", "congestion", "runway", "tarmac"],
    ("flights", "taxi_in"): ["taxi", "tarmac"],
    ("flights", "arr_delayed_15"): ["late", "on-time", "on time", "delayed", "punctual",
                                    "15 minutes"],
    ("flights", "dep_delayed_15"): ["late", "on-time", "on time", "delayed", "15 minutes"],
    ("flights", "distance"): ["distance", "miles", "long", "short", "far", "haul"],
    ("flights", "air_time"): ["air time", "airborne", "in the air", "duration"],
    ("flights", "actual_elapsed_time"): ["duration", "how long", "elapsed"],
    ("flights", "sched_elapsed_time"): ["scheduled duration", "padding", "schedule"],
    ("flights", "carrier_delay"): ["cause", "caused", "carrier", "reason", "blame"],
    ("flights", "weather_delay"): ["weather", "storm", "snow", "cause", "reason"],
    ("flights", "nas_delay"): ["air traffic", "atc", "nas", "system", "cause", "reason"],
    ("flights", "security_delay"): ["security", "cause", "reason"],
    ("flights", "late_aircraft_delay"): ["late aircraft", "previous flight", "cascade",
                                         "knock-on", "cause", "reason"],
    ("flights", "tail_number"): ["aircraft", "tail", "plane"],
    ("flights", "flight_number"): ["flight number"],
    ("flights", "origin_state"): ["state"],
    ("flights", "dest_state"): ["state"],
    ("flights", "origin_city"): ["city"],
    ("flights", "dest_city"): ["city"],
}

LOOKUP_TRIGGERS = {
    "airlines": ["airline", "carrier", "name", "delta", "american", "united", "southwest",
                 "jetblue", "alaska", "spirit", "frontier", "allegiant", "hawaiian",
                 "skywest", "republic", "envoy", "endeavor", "psa", "mesa"],
    "airports": ["airport name", "name of", "latitude", "longitude", "located", "location",
                 "international", "coordinates"],
}


def _tokens(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9\- ]+", " ", text.lower()) + " "


def _hit(text: str, word: str) -> bool:
    return f" {word}" in text  # prefix match: "cancel" matches "cancellations"


def select_columns(question: str, catalog: Catalog) -> dict[str, list[str]]:
    q = _tokens(question)
    selected: dict[str, list[str]] = {t: list(cols) for t, cols in CORE.items()}

    flights = catalog.tables.get("flights")
    if flights:
        for col in flights.columns:
            if col.name in selected["flights"]:
                continue
            words = KEYWORDS.get(("flights", col.name), []) + [col.name.replace("_", " ")]
            if any(_hit(q, w) for w in words):
                selected["flights"].append(col.name)

    for table, triggers in LOOKUP_TRIGGERS.items():
        if table in catalog.tables and any(_hit(q, w) for w in triggers):
            selected[table] = [c.name for c in catalog.tables[table].columns]

    # Keep catalog column order so the prompt reads naturally.
    return {
        t: [c.name for c in catalog.tables[t].columns if c.name in cols]
        for t, cols in selected.items() if t in catalog.tables
    }


def build_context(question: str, catalog: Catalog, mode: str = "retrieved",
                  docs: bool = True) -> tuple[str, dict]:
    """Return (schema text for the prompt, {table: [columns]} that were shown)."""
    if mode == "full":
        shown = {t.name: [c.name for c in t.columns] for t in catalog.tables.values()}
        return catalog.render(docs=docs), shown
    shown = select_columns(question, catalog)
    return catalog.render(columns=shown, docs=docs), shown
