"""GET /api/v1/reviews/due: the review queue the Talk/Review UI and the /progress
skill read. A thin HTTP surface over queries.due_words (#14) - due cards soonest
first, each joined to its Word (lemma, level). Future-dated cards must not leak in,
and an empty queue is [] with a 200, never an error."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.learner.db import Base, get_db, make_engine
from app.learner.models import Word, WordState
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


def _due_card(db, lemma: str, level: str, due: datetime) -> None:
    """Seed one Word + its FSRS WordState with an explicit due time."""
    word = Word(
        lemma=lemma, lemma_raw=lemma, pos="verb", level=level, source="glossary"
    )
    db.add(word)
    db.flush()
    db.add(WordState(word_id=word.id, due=due, state=2, card_json="{}"))


def test_due_returns_only_past_cards_soonest_first(client):
    http, TestSession = client
    now = datetime.now(UTC)
    with TestSession() as db:
        _due_card(db, "kommen", "A2", now - timedelta(hours=1))  # due, most recent
        _due_card(db, "gehen", "A2", now - timedelta(days=2))  # due, oldest
        _due_card(db, "erwaegen", "B2", now + timedelta(days=1))  # future, excluded
        db.commit()

    response = http.get("/api/v1/reviews/due")

    assert response.status_code == 200
    body = response.json()
    # Future card excluded; the two due cards ordered soonest-due (oldest due) first.
    assert [c["lemma"] for c in body] == ["gehen", "kommen"]
    assert body[0]["level"] == "A2"
    assert {"word_id", "lemma", "level", "due", "state"} <= set(body[0])


def test_due_empty_queue_is_200_and_empty_list(client):
    http, _ = client
    response = http.get("/api/v1/reviews/due")
    assert response.status_code == 200
    assert response.json() == []


def test_due_future_only_queue_is_empty(client):
    http, TestSession = client
    with TestSession() as db:
        _due_card(db, "erwaegen", "B2", datetime.now(UTC) + timedelta(days=3))
        db.commit()

    response = http.get("/api/v1/reviews/due")

    assert response.status_code == 200
    assert response.json() == []


def test_due_limit_returns_only_the_soonest(client):
    http, TestSession = client
    now = datetime.now(UTC)
    with TestSession() as db:
        _due_card(db, "kommen", "A2", now - timedelta(hours=1))
        _due_card(db, "gehen", "A2", now - timedelta(days=2))
        db.commit()

    response = http.get("/api/v1/reviews/due", params={"limit": 1})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["lemma"] == "gehen"  # the soonest-due of the two


def test_due_rejects_non_positive_limit(client):
    http, _ = client
    response = http.get("/api/v1/reviews/due", params={"limit": 0})
    assert response.status_code == 422


def test_due_declares_a_real_response_schema():
    app = create_app(frontend_dist=Path("no-frontend-build"))
    schema = app.openapi()["paths"]["/api/v1/reviews/due"]["get"]
    items = schema["responses"]["200"]["content"]["application/json"]["schema"]
    model = items["items"]["$ref"].rsplit("/", 1)[-1]
    props = app.openapi()["components"]["schemas"][model]["properties"]
    assert {"word_id", "lemma", "level", "due", "state"} <= set(props)
