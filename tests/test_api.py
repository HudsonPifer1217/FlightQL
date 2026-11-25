from conftest import FakeLLM, resp
from fastapi.testclient import TestClient

from flightql.agent import FlightQLAgent
from flightql.api import create_app


def test_ask_endpoint_returns_answer_sql_and_trail(settings, con):
    llm = FakeLLM([resp("SELECT origin, COUNT(*) AS n FROM flights GROUP BY origin",
                        ["flights.origin"])])
    app = create_app(FlightQLAgent(llm, settings, con=con))
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert "flights" in client.get("/schema").json()
        body = client.post("/ask", json={"question": "Flights per origin?"}).json()
    assert body["status"] == "answered"
    assert body["sql"].startswith("SELECT")
    assert body["columns"] == ["origin", "n"]
    assert body["attempts"][0]["validation_errors"] == []


def test_ask_rejects_empty_question(settings, con):
    app = create_app(FlightQLAgent(FakeLLM([]), settings, con=con))
    with TestClient(app) as client:
        assert client.post("/ask", json={"question": ""}).status_code == 422


def test_schema_mode_override_does_not_touch_shared_settings(settings, con):
    llm = FakeLLM([resp("SELECT COUNT(*) AS n FROM flights", [])] * 2)
    agent = FlightQLAgent(llm, settings, con=con)
    before = agent.settings.schema_mode
    app = create_app(agent)
    with TestClient(app) as client:
        body = client.post("/ask", json={"question": "How many flights?",
                                         "schema_mode": "full"}).json()
    assert body["status"] == "answered"
    assert set(body["schema_shown"]) == set(agent.catalog.tables)  # full schema was used
    assert agent.settings.schema_mode == before                    # and nothing was mutated
