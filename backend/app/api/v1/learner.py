"""Learner endpoints. For now just manual immersion logging.

Hours are the primary metric and most of them happen away from the app - a
wife-call segment, a bakery mission. This is where those get counted, so the
/progress numbers reflect the whole day and not only screen time.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DbSession

from app.learner.db import get_db
from app.learner.models import ImmersionLog
from app.learner.models import Session as SessionRow

router = APIRouter(tags=["learner"])


class LogHoursRequest(BaseModel):
    source: Literal["call", "mission"] = Field(
        description="Where the German time happened. Only the manual sources: a "
        "wife-call segment or a real-life mission. app hours are written by the "
        "session loop itself, so accepting them here would double-count them."
    )
    minutes: int = Field(gt=0, le=600, description="Minutes of German, 1-600.")
    note: str | None = Field(
        default=None,
        max_length=280,
        description="Optional context, e.g. 'Termin beim Arzt'.",
    )


class ImmersionLogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    minutes: int
    note: str | None
    session_id: int | None
    created_at: datetime


@router.post(
    "/log-hours",
    response_model=ImmersionLogEntry,
    status_code=201,
    summary="Log a block of immersion time",
)
def log_hours(
    body: LogHoursRequest, db: DbSession = Depends(get_db)
) -> ImmersionLogEntry:
    entry = ImmersionLog(source=body.source, minutes=body.minutes, note=body.note)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return ImmersionLogEntry.model_validate(entry)


class ErrorEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    error_type: str
    severity: str
    utterance: str
    correction: str
    explanation: str | None
    created_at: datetime


class SessionDebrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scenario: str | None
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    errors: list[ErrorEntry]


@router.get(
    "/sessions/{session_id}",
    response_model=SessionDebrief,
    summary="Fetch a session's debrief: duration and every error caught",
)
def get_session_debrief(
    session_id: int, db: DbSession = Depends(get_db)
) -> SessionDebrief:
    session = db.get(SessionRow, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Unknown session {session_id}.")
    return SessionDebrief.model_validate(session)
