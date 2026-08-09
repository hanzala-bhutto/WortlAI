"""The py-fsrs quarantine wrapper: fresh cards, review round-trips, UTC-only (#14)."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fsrs import Rating

from app.learner import fsrs_engine


def _t(hour: int = 12) -> datetime:
    return datetime(2026, 8, 8, hour, 0, tzinfo=UTC)


def test_first_review_creates_learning_card():
    result = fsrs_engine.review(None, Rating.Good, _t())
    assert result.due > _t()
    assert result.state in (1, 2, 3)
    assert json.loads(result.card_json)  # round-trippable


def test_second_success_pushes_due_further_out():
    first = fsrs_engine.review(None, Rating.Good, _t(12))
    second = fsrs_engine.review(first.card_json, Rating.Good, _t(13))
    assert second.due > first.due


def test_again_reschedules_within_a_day():
    good = fsrs_engine.review(None, Rating.Good, _t(12))
    again = fsrs_engine.review(good.card_json, Rating.Again, _t(13))
    assert again.due - _t(13) < timedelta(days=1)


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError):
        fsrs_engine.review(None, Rating.Good, datetime(2026, 8, 8, 12, 0))
