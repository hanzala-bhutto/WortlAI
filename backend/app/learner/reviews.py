"""Fold a finished session's conversation signals into FSRS state.

The write side of #14: it ties the grade mapper (grading.py), the scheduler
wrapper (fsrs_engine.py) and the two tables together. Given the words a session
touched and how each was used, it advances every card and appends a review event.
Signal *classification* - turning a transcript into these WordSignals via spaCy
lemmatization and unprompted-vs-gloss detection - is the session-end path's job,
not this one, so apply_session_reviews takes the already classified list and the
scheduling stays testable without a live conversation.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.learner import fsrs_engine
from app.learner.grading import Signal, signal_to_rating
from app.learner.models import ReviewEvent, WordState


class WordSignal(NamedTuple):
    """One word a session touched and how it was used. `unprompted_uses` only bears
    on an UNPROMPTED signal (Good vs Easy); other signals ignore it."""

    word_id: int
    signal: Signal
    unprompted_uses: int = 1


def apply_session_reviews(
    db: DbSession,
    signals: Sequence[WordSignal],
    session_id: int | None = None,
    when: datetime | None = None,
) -> None:
    """Advance each touched word's FSRS card and log the review. Creates a card on
    first sight (new cards are due immediately), round-trips an existing one. Rows
    are added to the session but not committed - the caller owns the transaction,
    matching record_session_immersion."""
    when = when or datetime.now(UTC)
    for touch in signals:
        rating = signal_to_rating(touch.signal, touch.unprompted_uses)
        state = db.scalars(
            select(WordState).where(WordState.word_id == touch.word_id)
        ).one_or_none()
        result = fsrs_engine.review(
            None if state is None else state.card_json, rating, when
        )
        if state is None:
            db.add(
                WordState(
                    word_id=touch.word_id,
                    due=result.due,
                    state=result.state,
                    last_reviewed_at=when,
                    card_json=result.card_json,
                )
            )
        else:
            state.due = result.due
            state.state = result.state
            state.last_reviewed_at = when
            state.card_json = result.card_json
        db.add(
            ReviewEvent(
                word_id=touch.word_id,
                session_id=session_id,
                rating=rating.value,
                signal=touch.signal.value,
                reviewed_at=when,
            )
        )
