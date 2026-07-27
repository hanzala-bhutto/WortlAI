"""GET /api/v1/sessions/{id}: the debrief the Talk UI fetches after a session
closes - duration and every error the Corrector caught, never shown mid-session
(pedagogy rule: errors go to the debrief, not the conversation)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.learner.db import Base, get_db, make_engine
from app.learner.models import ErrorLog, Session
from app.main import create_app


@pytest.fixture
def client(tmp_path):
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


def _closed_session_with_one_error(TestSession) -> int:
    with TestSession() as db:
        session = Session(scenario="baeckerei")
        session.mark_ended()
        db.add(session)
        db.flush()
        db.add(
            ErrorLog(
                session_id=session.id,
                error_type="grammar.case.dative",
                severity="minor",
                utterance="Ich gebe dem Mann das Brot falsch",
                correction="Ich gebe dem Mann das Brot",
                explanation="Extra word, not a case error.",
            )
        )
        db.commit()
        return session.id


def test_debrief_returns_duration_and_errors(client):
    http, TestSession = client
    session_id = _closed_session_with_one_error(TestSession)

    response = http.get(f"/api/v1/sessions/{session_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == session_id
    assert body["scenario"] == "baeckerei"
    assert body["duration_seconds"] is not None
    assert len(body["errors"]) == 1
    error = body["errors"][0]
    assert error["error_type"] == "grammar.case.dative"
    assert error["correction"] == "Ich gebe dem Mann das Brot"
    assert error["explanation"] == "Extra word, not a case error."


def test_debrief_for_a_still_open_session_has_no_duration(client):
    http, TestSession = client
    with TestSession() as db:
        session = Session(scenario="cafe")
        db.add(session)
        db.commit()
        session_id = session.id

    response = http.get(f"/api/v1/sessions/{session_id}")

    assert response.status_code == 200
    assert response.json()["duration_seconds"] is None
    assert response.json()["errors"] == []


def test_debrief_404s_for_an_unknown_session(client):
    http, _ = client
    response = http.get("/api/v1/sessions/999")
    assert response.status_code == 404


def test_debrief_declares_a_real_response_schema():
    app = create_app(frontend_dist=Path("no-frontend-build"))
    schema = app.openapi()["paths"]["/api/v1/sessions/{session_id}"]["get"]
    ref = schema["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    model = ref.rsplit("/", 1)[-1]
    props = app.openapi()["components"]["schemas"][model]["properties"]
    assert {"id", "scenario", "duration_seconds", "errors"} <= set(props)
