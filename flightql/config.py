"""Runtime settings, read from the environment (and a local .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DEFAULT_MODEL = "claude-opus-5-5"

# USD per million tokens (input, output). Used for per-question cost reporting.
PRICING = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


@dataclass
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(os.getenv("FLIGHTQL_DB", ROOT / "data" / "flights.duckdb"))
    )
    model: str = field(default_factory=lambda: os.getenv("FLIGHTQL_MODEL", DEFAULT_MODEL))
    schema_mode: str = "retrieved"  # "retrieved" (relevant columns only) or "full"
    data_dictionary: bool = True    # include hand-written column notes in the prompt
    max_retries: int = 2
    max_rows: int = 1000
    query_timeout_s: float = 30.0
    summarize: bool = True
    log_dir: Path = ROOT / "logs"
