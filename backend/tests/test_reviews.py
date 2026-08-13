"""apply_session_reviews folds signals into FSRS state; due_words reads the queue (#14)."""

from datetime import UTC, datetime, timedelta

from fsrs import Rating
from sqlalchemy import select

from app.learner.grading import Signal
from app.learner.models import ReviewEvent, Session, Word, WordState
from app.learner.queries import due_words
from app.learner.reviews import WordSignal, apply_session_reviews


def _word(db, lemma: str = "gehen") -> Word:
    word = Word(
        lemma=lemma,
        lemma_raw=lemma,
        pos="verb",
        translation_en="to go",
        level="A2",
        source="glossary",
    )
    db.add(word)
    db.commit()
    return word


def test_first_signal_creates_state_and_event(db_session):
    word = _word(db_session)
    apply_session_reviews(db_session, [WordSignal(word.id, Signal.UNPROMPTED)])
    db_session.commit()

    state = db_session.scalars(select(WordState)).one()
    assert state.word_id == word.id
    assert state.card_json
    event = db_session.scalars(select(ReviewEvent)).one()
    assert event.rating == Rating.Good.value
    assert event.signal == "unprompted"


def test_second_signal_updates_state_in_place(db_session):
    word = _word(db_session)
    when = datetime(2026, 8, 8, 12, tzinfo=UTC)
    apply_session_reviews(
        db_session, [WordSignal(word.id, Signal.UNPROMPTED)], when=when
    )
    db_session.commit()
    apply_session_reviews(
        db_session,
        [WordSignal(word.id, Signal.UNPROMPTED)],
        when=when + timedelta(hours=1),
    )
    db_session.commit()

    assert len(db_session.scalars(select(WordState)).all()) == 1  # updated, not dup'd
    assert len(db_session.scalars(select(ReviewEvent)).all()) == 2


def test_due_words_lists_due_cards_soonest_first(db_session):
    w1 = _word(db_session, "gehen")
    w2 = _word(db_session, "kommen")
    when = datetime(2026, 8, 8, 12, tzinfo=UTC)
    apply_session_reviews(
        db_session,
        [WordSignal(w1.id, Signal.FAILED), WordSignal(w2.id, Signal.UNPROMPTED)],
        when=when,
    )
    db_session.commit()

    due = due_words(db_session, now=when + timedelta(days=2))
    assert {s.word_id for s in due} == {w1.id, w2.id}
    assert due[0].due <= due[1].due  # ascending by due


def test_due_words_excludes_cards_scheduled_into_the_future(db_session):
    """A Good review pushes the card's due past the review time, so the queue at
    that instant is empty and fills only once due arrives - the half of "due queue
    correct" a broken `due <= now` filter would silently pass."""
    word = _word(db_session)
    when = datetime(2026, 8, 8, 12, tzinfo=UTC)
    apply_session_reviews(
        db_session, [WordSignal(word.id, Signal.UNPROMPTED)], when=when
    )
    db_session.commit()

    assert due_words(db_session, now=when) == []  # due is in the future
    assert [s.word_id for s in due_words(db_session, now=when + timedelta(days=2))] == [
        word.id
    ]


def test_due_words_honours_limit(db_session):
    when = datetime(2026, 8, 8, 12, tzinfo=UTC)
    signals = [
        WordSignal(_word(db_session, f"wort{i}").id, Signal.FAILED) for i in range(3)
    ]
    apply_session_reviews(db_session, signals, when=when)
    db_session.commit()

    assert len(due_words(db_session, now=when + timedelta(days=2), limit=2)) == 2


def test_deleting_session_keeps_events_and_nulls_the_link(db_session):
    session = Session()
    db_session.add(session)
    db_session.commit()
    word = _word(db_session)
    apply_session_reviews(
        db_session, [WordSignal(word.id, Signal.UNPROMPTED)], session_id=session.id
    )
    db_session.commit()

    db_session.delete(session)
    db_session.commit()

    event = db_session.scalars(select(ReviewEvent)).one()
    assert event.session_id is None  # review history outlives the conversation
