"""HTTP API.  Run with:  uvicorn flightql.api:app --reload

POST /ask     {"question": "..."}  -> answer, SQL, result rows, validation trail
GET  /schema  the tables and columns the model is allowed to use
GET  /health
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .agent import AgentResult, FlightQLAgent


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    schema_mode: Literal["retrieved", "full"] | None = None


def create_app(agent: FlightQLAgent | None = None) -> FastAPI:
    state: dict = {"agent": agent}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if state["agent"] is None:
            from .config import Settings
            from .generate import AnthropicLLM
            settings = Settings()
            state["agent"] = FlightQLAgent(AnthropicLLM(settings.model), settings)
        yield

    app = FastAPI(title="FlightQL", version="0.1.0", lifespan=lifespan,
                  description="Natural-language questions over U.S. flight data, answered "
                              "with SQL that is validated before it runs.")

    def get_agent() -> FlightQLAgent:
        if state["agent"] is None:
            raise HTTPException(503, "agent not initialized")
        return state["agent"]

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/schema")
    def schema():
        cat = get_agent().catalog
        return {t.name: {"description": t.description,
                         "columns": {c.name: c.type for c in t.columns}}
                for t in cat.tables.values()}

    # Sync handler: FastAPI runs it in a threadpool, so the blocking LLM and
    # DuckDB calls don't stall the event loop.
    @app.post("/ask", response_model=AgentResult)
    def ask(req: AskRequest):
        return get_agent().ask(req.question, schema_mode=req.schema_mode)

    return app


app = create_app()
