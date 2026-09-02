"""The read-side aggregations the /progress skill depends on."""

from datetime import UTC, datetime

from app.learner.models import ErrorLog, ImmersionLog, Session, Word
from app.learner.queries import (
    error_counts_by_type,
    hours_per_day,
    record_session_immersion,
    words_at_level,
)


def _word(db, lemma: str, level: str) -> Word:
    word = Word(
        lemma=lemma, lemma_raw=lemma, pos="verb", level=level, source="glossary"
    )
    db.add(word)
    return word


def _day(y, m, d) -> datetime:
    return datetime(y, m, d, 12, 0, tzinfo=UTC)


def test_hours_per_day_sums_every_source_by_day(db_session):
    db_session.add_all(
        [
            ImmersionLog(source="app", minutes=20, created_at=_day(2026, 7, 24)),
            ImmersionLog(source="call", minutes=30, created_at=_day(2026, 7, 24)),
            ImmersionLog(source="mission", minutes=15, created_at=_day(2026, 7, 25)),
        ]
    )
    db_session.commit()

    result = hours_per_day(db_session)

    assert result == [("2026-07-24", 50), ("2026-07-25", 15)]


def test_hours_per_day_since_filters_older_rows(db_session):
    db_session.add_all(
        [
            ImmersionLog(source="app", minutes=40, created_at=_day(2026, 7, 20)),
            ImmersionLog(source="app", minutes=10, created_at=_day(2026, 7, 25)),
        ]
    )
    db_session.commit()

    result = hours_per_day(db_session, since=_day(2026, 7, 22))

    assert result == [("2026-07-25", 10)]


def test_record_session_immersion_folds_a_session_into_the_metric(db_session):
    session = Session()
    db_session.add(session)
    db_session.commit()

    record_session_immersion(db_session, session_id=session.id, minutes=18)
    db_session.commit()

    row = db_session.query(ImmersionLog).one()
    assert row.source == "app"
    assert row.minutes == 18
    assert row.session_id == session.id


def test_words_at_level_returns_only_that_levels_deck(db_session):
    a2_one = _word(db_session, "gehen", "A2")
    a2_two = _word(db_session, "kommen", "A2")
    _word(db_session, "erwaegen", "B2")  # a higher-level word must not leak in
    db_session.commit()

    result = words_at_level(db_session, "A2")

    assert [w.id for w in result] == [a2_one.id, a2_two.id]  # ordered by id


def test_words_at_level_is_empty_when_the_deck_has_no_such_level(db_session):
    _word(db_session, "gehen", "A2")
    db_session.commit()

    assert words_at_level(db_session, "B1") == []


def test_error_counts_rank_types_by_frequency(db_session):
    session = Session()
    session.errors.extend(
        [
            ErrorLog(
                error_type="grammar.case",
                severity="critical",
                utterance="a",
                correction="b",
            ),
            ErrorLog(
                error_type="grammar.case",
                severity="minor",
                utterance="c",
                correction="d",
            ),
            ErrorLog(
                error_type="vocab", severity="minor", utterance="e", correction="f"
            ),
        ]
    )
    db_session.add(session)
    db_session.commit()

    result = error_counts_by_type(db_session)

    assert result == [("grammar.case", 2), ("vocab", 1)]
