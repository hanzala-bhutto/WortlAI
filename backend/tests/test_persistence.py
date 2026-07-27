"""Contract for writing a conversation into the learner store.

This is the box #6 deliberately left unchecked: a session and its errors written
during a real conversation. The behaviour that matters is that a session opens
with an id the graph can hang errors off, closes with a timestamp, writes only
validated error rows, and folds its own elapsed time into the one immersion metric
without double counting.

Runs against a temp learner DB (a real file, so WAL and FK enforcement behave as
in production), never the app's wortlai.db.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.agents.persistence import SessionWriter
from app.learner.db import Base, make_engine
from app.learner.models import ImmersionLog, Session


@pytest.fixture
def factory(tmp_path):
    """A session factory over a fresh temp learner DB, plus teardown."""
    engine = make_engine(f"sqlite:///{tmp_path / 'w.db'}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False, future=True)
    engine.dispose()


def test_create_session_returns_id_and_leaves_it_open(factory):
    writer = SessionWriter(session_factory=factory)

    sid = writer.create_session("baeckerei")

    assert isinstance(sid, int)
    with factory() as db:
        row = db.get(Session, sid)
        assert row.scenario == "baeckerei"
        assert row.ended_at is None


def test_end_session_stamps_ended_and_writes_validated_errors(factory):
    writer = SessionWriter(session_factory=factory)
    sid = writer.create_session("cafe")
    errors = [
        {
            "error_type": "grammar.case.dative",
            "severity": "minor",
            "utterance": "mit der Hund",
            "correction": "mit dem Hund",
        }
    ]

    writer.end_session(sid, errors=errors)

    with factory() as db:
        row = db.get(Session, sid)
        assert row.ended_at is not None
        assert len(row.errors) == 1
        assert row.errors[0].error_type == "grammar.case.dative"
        assert row.errors[0].correction == "mit dem Hund"


def test_end_session_folds_elapsed_minutes_into_immersion(factory):
    writer = SessionWriter(session_factory=factory)
    started = datetime.now(UTC) - timedelta(minutes=25)
    sid = writer.create_session("wohnung", started_at=started)

    writer.end_session(sid)

    with factory() as db:
        rows = db.scalars(select(ImmersionLog)).all()
        assert len(rows) == 1
        assert rows[0].source == "app"
        assert rows[0].session_id == sid
        assert 24 <= rows[0].minutes <= 26  # slack for the wall-clock in end_session


def test_end_session_does_not_log_a_zero_minute_block(factory):
    writer = SessionWriter(session_factory=factory)
    sid = writer.create_session("baeckerei")  # started just now -> under a minute

    writer.end_session(sid)

    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ImmersionLog)) == 0


def test_end_unknown_session_raises(factory):
    writer = SessionWriter(session_factory=factory)

    with pytest.raises(KeyError):
        writer.end_session(999)
