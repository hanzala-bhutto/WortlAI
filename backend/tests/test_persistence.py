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
from app.learner.models import ImmersionLog, ReviewEvent, Session, Word, WordState


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


def _seed_deck(factory, *lemmas: str, level: str = "A2") -> None:
    with factory() as db:
        db.add_all(
            Word(lemma=w, lemma_raw=w, pos="verb", level=level, source="glossary")
            for w in lemmas
        )
        db.commit()


def test_end_session_folds_the_transcript_into_fsrs_state(factory):
    """AC-4: a stubbed transcript drives the close and the expected word_states /
    review_events rows appear, written in the same transaction as the close."""
    _seed_deck(factory, "gehen", "kaufen")
    writer = SessionWriter(session_factory=factory)
    started = datetime.now(UTC) - timedelta(minutes=20)
    ended = datetime.now(UTC) - timedelta(minutes=2)
    sid = writer.create_session("cafe", started_at=started)

    writer.end_session(
        sid,
        ended_at=ended,
        messages=[{"role": "user", "content": "Ich gehe einkaufen und kaufe Brot."}],
        level="A2",
    )

    with factory() as db:
        assert db.get(Session, sid).ended_at is not None  # closed
        states = db.scalars(select(WordState)).all()
        events = db.scalars(select(ReviewEvent)).all()
        assert len(states) == 2  # both produced words carded
        assert len(events) == 2
        assert all(e.session_id == sid for e in events)  # events point at the close
        # Reviews are stamped at the session's end time, not wall-clock now()
        # (SQLite may drop tzinfo on the round-trip, so compare naive to naive).
        assert all(
            e.reviewed_at.replace(tzinfo=None) == ended.replace(tzinfo=None)
            for e in events
        )


def test_end_session_with_no_scheduled_items_is_a_clean_no_op(factory):
    """AC-2: a transcript that produces no deck word closes cleanly, no review rows."""
    _seed_deck(factory, "gehen")
    writer = SessionWriter(session_factory=factory)
    sid = writer.create_session("cafe")

    writer.end_session(
        sid, messages=[{"role": "user", "content": "Okay, tschüss."}], level="A2"
    )

    with factory() as db:
        assert db.get(Session, sid).ended_at is not None
        assert db.scalars(select(WordState)).all() == []
        assert db.scalars(select(ReviewEvent)).all() == []


def test_end_session_without_a_transcript_skips_the_fold(factory):
    """A session that never reached converse (no messages/level) still closes."""
    _seed_deck(factory, "gehen")
    writer = SessionWriter(session_factory=factory)
    sid = writer.create_session("cafe")

    writer.end_session(sid)  # no messages, no level

    with factory() as db:
        assert db.get(Session, sid).ended_at is not None
        assert db.scalars(select(WordState)).all() == []


def test_a_failing_fold_never_blocks_the_close(factory, monkeypatch):
    """AC-3 / guardrail 4: if the fold blows up entirely (e.g. the spaCy model fails
    to load), the session still closes (ended_at, immersion written). This pins the
    end_session try/except; the savepoint's partial-rollback is pinned separately."""
    _seed_deck(factory, "gehen")
    writer = SessionWriter(session_factory=factory)
    started = datetime.now(UTC) - timedelta(minutes=12)
    sid = writer.create_session("cafe", started_at=started)

    def boom(*_args, **_kwargs):
        raise RuntimeError("spaCy model failed to load")

    monkeypatch.setattr("app.agents.persistence.fold_session_reviews", boom)

    writer.end_session(
        sid,
        messages=[{"role": "user", "content": "Ich gehe zur Arbeit."}],
        level="A2",
    )

    with factory() as db:
        assert db.get(Session, sid).ended_at is not None  # closed despite the failure
        assert db.scalars(select(ImmersionLog)).all()  # immersion still folded
        assert db.scalars(select(WordState)).all() == []
        assert db.scalars(select(ReviewEvent)).all() == []


def test_a_mid_batch_scheduler_failure_rolls_back_the_whole_fold(factory, monkeypatch):
    """The savepoint's reason to exist: if scheduling writes one card then raises, the
    already-written row must NOT commit with the close. A bare try/except (no savepoint)
    would leave that first WordState behind - this is what tells the two apart."""
    _seed_deck(factory, "gehen")
    writer = SessionWriter(session_factory=factory)
    started = datetime.now(UTC) - timedelta(minutes=12)
    sid = writer.create_session("cafe", started_at=started)

    def write_one_then_fail(db, signals, session_id=None, when=None):
        # A real partial write: flush a card into the savepoint, then blow up.
        db.add(
            WordState(
                word_id=signals[0].word_id,
                due=datetime.now(UTC),
                state=1,
                card_json="{}",
            )
        )
        db.flush()
        raise RuntimeError("scheduler failed mid-batch")

    monkeypatch.setattr(
        "app.learner.signals.apply_session_reviews", write_one_then_fail
    )

    writer.end_session(
        sid,
        messages=[{"role": "user", "content": "Ich gehe zur Arbeit."}],
        level="A2",
    )

    with factory() as db:
        assert db.get(Session, sid).ended_at is not None  # close survived
        assert db.scalars(select(ImmersionLog)).all()  # immersion committed
        assert db.scalars(select(WordState)).all() == []  # partial write rolled back
        assert db.scalars(select(ReviewEvent)).all() == []
