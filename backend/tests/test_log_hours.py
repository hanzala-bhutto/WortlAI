"""POST /api/v1/log-hours: manual immersion logging for call and mission time."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.learner.db import Base, get_db, make_engine
from app.learner.models import ImmersionLog
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    """A test client backed by a throwaway DB. get_db is overridden so no
    migration or real data file is touched, and the app is not entered as a
    context manager, so the startup migration never fires."""
    engine = make_engine(f"sqlite:///{tmp_path / 'api.db'}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    def override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app = create_app(frontend_dist=Path("no-frontend-build"))
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app), TestSession
    engine.dispose()


def test_log_hours_persists_a_call_segment(client):
    http, TestSession = client

    response = http.post(
        "/api/v1/log-hours",
        json={"source": "call", "minutes": 25, "note": "wife call"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "call"
    assert body["minutes"] == 25
    assert body["note"] == "wife call"
    assert body["session_id"] is None
    assert "created_at" in body and body["id"] > 0

    with TestSession() as db:
        assert db.query(ImmersionLog).count() == 1


@pytest.mark.parametrize("source", ["youtube", "app"])
def test_log_hours_rejects_non_manual_sources(client, source):
    """Unknown sources and app (written by the session loop, so a manual entry
    would double-count it) are both refused."""
    http, _ = client
    response = http.post("/api/v1/log-hours", json={"source": source, "minutes": 10})
    assert response.status_code == 422


@pytest.mark.parametrize("minutes", [0, -5, 601])
def test_log_hours_rejects_out_of_range_minutes(client, minutes):
    """A stuck loop or a fat-fingered entry must not corrupt the hours metric."""
    http, _ = client
    response = http.post(
        "/api/v1/log-hours", json={"source": "mission", "minutes": minutes}
    )
    assert response.status_code == 422


def test_log_hours_declares_a_real_response_schema():
    """No `-> dict`: the frontend gets a typed contract for the created entry."""
    app = create_app(frontend_dist=Path("no-frontend-build"))
    schema = app.openapi()["paths"]["/api/v1/log-hours"]["post"]
    ref = schema["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
    model = ref.rsplit("/", 1)[-1]
    props = app.openapi()["components"]["schemas"][model]["properties"]
    assert {"id", "source", "minutes", "session_id", "created_at"} <= set(props)
