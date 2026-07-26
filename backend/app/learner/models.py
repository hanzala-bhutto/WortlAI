"""The learner store's tables: sessions, error log, immersion hours.

Kept intentionally close to the domain: a Session is one conversation, an
ErrorLog row is one mistake the Corrector caught in it, an ImmersionLog row is a
block of German time from any source. Later phases hang FSRS cards and the
lexical graph off the same Base; nothing here presumes them.

Enumerated columns (severity, source) are plain strings validated at the API
boundary, so widening the taxonomy is an app change, not a migration.
"""

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.learner.db import Base

# Vocabulary these string columns carry, enforced at the boundary that writes
# them (the /log-hours Literal today, the Corrector's schema in #5), not here -
# plain strings keep the taxonomy wide-able without a migration:
#   ImmersionLog.source: "app" | "call" | "mission"
#   ErrorLog.severity:   "critical" | "minor"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Session(Base):
    """One conversation. Open while it runs (ended_at is null), closed when it
    finishes. Duration is derived from the two timestamps, not stored - one fact,
    one place, no drift."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(default=None)
    scenario: Mapped[str | None] = mapped_column(String(120), default=None)

    errors: Mapped[list["ErrorLog"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )
    immersion: Mapped[list["ImmersionLog"]] = relationship(
        back_populates="session",
        # App-sourced hours belong to a session; deleting the session takes them
        # with it. Call/mission rows have no session and are untouched.
        cascade="all, delete-orphan",
    )

    def mark_ended(self, when: datetime | None = None) -> None:
        """Close the session by stamping when it finished."""
        self.ended_at = when or _utcnow()

    @property
    def duration_seconds(self) -> int | None:
        """How long the conversation lasted, or None while it is still open.
        Derived on read so it can never disagree with the timestamps."""
        if self.ended_at is None:
            return None
        started, ended = self.started_at, self.ended_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if ended.tzinfo is None:
            ended = ended.replace(tzinfo=UTC)
        return max(0, int((ended - started).total_seconds()))


class ErrorLog(Base):
    """One mistake caught in a session: what was said, the fix, and how much it
    mattered. The correction is what a card or a debrief item is built from."""

    __tablename__ = "error_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    # A dotted taxonomy path, e.g. "grammar.case.dative" or "vocab.false-friend".
    error_type: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    utterance: Mapped[str] = mapped_column(Text)
    correction: Mapped[str] = mapped_column(Text)

    session: Mapped[Session] = relationship(back_populates="errors")


class ImmersionLog(Base):
    """A block of German time. app-sourced rows link to the session that produced
    them; call and mission rows are logged by hand and stand alone."""

    __tablename__ = "immersion_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    minutes: Mapped[int] = mapped_column()
    note: Mapped[str | None] = mapped_column(String(280), default=None)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), default=None, index=True
    )

    session: Mapped[Session | None] = relationship(back_populates="immersion")
