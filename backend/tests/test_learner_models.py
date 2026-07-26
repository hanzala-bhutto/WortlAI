"""The learner store's tables and the relationships between them."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.learner.models import ErrorLog, ImmersionLog, Session


def test_session_records_errors_and_they_come_back(db_session):
    session = Session(scenario="Termin beim Arzt")
    session.errors.append(
        ErrorLog(
            error_type="grammar.case.dative",
            severity="critical",
            utterance="Ich gehe zu der Arzt",
            correction="Ich gehe zum Arzt",
        )
    )
    db_session.add(session)
    db_session.commit()

    stored = db_session.scalars(select(Session)).one()
    assert stored.scenario == "Termin beim Arzt"
    assert len(stored.errors) == 1
    assert stored.errors[0].session_id == stored.id
    assert stored.errors[0].correction == "Ich gehe zum Arzt"


def test_deleting_a_session_takes_its_errors_and_app_hours_with_it(db_session):
    """Errors and app-sourced hours belong to their session; a manual call log
    does not and must survive."""
    session = Session()
    session.errors.append(
        ErrorLog(
            error_type="vocab",
            severity="minor",
            utterance="das Handy ist kaputt",
            correction="das Smartphone ist kaputt",
        )
    )
    session.immersion.append(ImmersionLog(source="app", minutes=12))
    db_session.add(session)
    db_session.add(ImmersionLog(source="call", minutes=30))  # standalone
    db_session.commit()

    db_session.delete(session)
    db_session.commit()

    assert db_session.scalars(select(ErrorLog)).all() == []
    surviving = db_session.scalars(select(ImmersionLog)).all()
    assert [row.source for row in surviving] == ["call"]


def test_mark_ended_freezes_a_non_negative_duration(db_session):
    started = datetime.now(UTC) - timedelta(minutes=15)
    session = Session(started_at=started)
    db_session.add(session)
    db_session.commit()

    session.mark_ended(started + timedelta(minutes=15))
    db_session.commit()

    assert session.ended_at is not None
    assert session.duration_seconds == 15 * 60


def test_error_row_requires_a_session(db_session):
    """The foreign key is enforced (PRAGMA foreign_keys=ON), so an orphan error
    with a bogus session_id is rejected rather than silently stored."""
    db_session.add(
        ErrorLog(
            session_id=9999,
            error_type="grammar",
            severity="minor",
            utterance="x",
            correction="y",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
