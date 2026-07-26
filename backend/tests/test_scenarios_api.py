"""Contract for the scenario picker endpoint.

The picker is what the Talk UI (#8) lists so a learner can choose a roleplay before
a session starts (acceptance criterion #3). It is a plain read of the registry with
a declared response_model, so the OpenAPI schema is real and the shape is stable.
"""

from fastapi.testclient import TestClient

from app.agents.scenarios import list_scenarios
from app.main import create_app

client = TestClient(create_app())


def test_lists_every_registered_scenario():
    response = client.get("/api/v1/scenarios")

    assert response.status_code == 200
    body = response.json()
    assert {s["id"] for s in body} == {s.id for s in list_scenarios()}


def test_each_entry_carries_the_picker_fields():
    response = client.get("/api/v1/scenarios")

    first = response.json()[0]
    assert set(first) == {"id", "title", "level", "redemittel"}
    assert isinstance(first["redemittel"], list)
    # Persona and opening line are the Tutor's business, not the picker's; they
    # must not leak into the public listing.
    assert "persona" not in first
    assert "opening_line" not in first


def test_order_matches_the_registry():
    response = client.get("/api/v1/scenarios")

    assert [s["id"] for s in response.json()] == [s.id for s in list_scenarios()]
