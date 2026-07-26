"""Read-side helpers the /progress skill leans on.

Kept out of the models so the aggregation lives in one place and can be tested
without an HTTP layer. All grouping is by UTC calendar day; the progress skill
(#27) is where a Dresden-local day boundary belongs, once there is a UI to show
it against the 2-month protocol.
"""

from datetime import datetime
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.learner.models import ErrorLog, ImmersionLog


class DayMinutes(NamedTuple):
    day: str
    minutes: int


class TypeCount(NamedTuple):
    error_type: str
    count: int


def hours_per_day(db: DbSession, since: datetime | None = None) -> list[DayMinutes]:
    """Immersion minutes per day across every source (app, call, mission), oldest
    day first. App sessions feed this by writing an app-source row on end, so
    this one query is the whole hours metric with no double counting."""
    day = func.date(ImmersionLog.created_at)
    stmt = (
        select(day.label("day"), func.sum(ImmersionLog.minutes).label("minutes"))
        .group_by(day)
        .order_by(day)
    )
    if since is not None:
        stmt = stmt.where(ImmersionLog.created_at >= since)
    return [DayMinutes(day=row.day, minutes=int(row.minutes)) for row in db.execute(stmt)]


def error_counts_by_type(
    db: DbSession, since: datetime | None = None
) -> list[TypeCount]:
    """How often each error type shows up, most frequent first - the trend a
    debrief and the curriculum both read to decide what to drill next."""
    stmt = (
        select(
            ErrorLog.error_type.label("error_type"),
            func.count().label("count"),
        )
        .group_by(ErrorLog.error_type)
        .order_by(func.count().desc(), ErrorLog.error_type)
    )
    if since is not None:
        stmt = stmt.where(ErrorLog.created_at >= since)
    return [
        TypeCount(error_type=row.error_type, count=int(row.count))
        for row in db.execute(stmt)
    ]


def record_session_immersion(db: DbSession, session_id: int, minutes: int) -> None:
    """Fold a finished session's time into the immersion metric as an app-source
    row. The one place app hours enter hours_per_day, so sessions and manual
    call/mission logs sum in the same table."""
    db.add(ImmersionLog(source="app", minutes=minutes, session_id=session_id))
