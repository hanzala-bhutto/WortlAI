"""Writing a conversation into the learner store.

This is the seam between the (async) session graph and the (sync) SQLAlchemy
learner store. The graph's setup node opens a Session row here; its debrief node
closes it, writes any errors the Corrector caught, and folds the elapsed time into
the immersion metric. Nodes call these through asyncio.to_thread, so the sync DB
work never blocks the event loop.

Guardrail #5: only validated fields are written. In v1 the error list is empty (the
Corrector is #5); when it arrives, each error is a dict of already-validated
columns, not free LLM text.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy.orm import Session as DbSession

from app.learner.db import SessionLocal
from app.learner.models import ErrorLog, Session
from app.learner.queries import record_session_immersion

# One caught error, as the already-validated columns of an error_logs row. A TypedDict
# would over-promise here; the Corrector (#5) owns the real schema.
ErrorRow = dict[str, object]


class SessionWriter:
    """Persists session lifecycle events. Takes a session factory so a test can
    point it at a temp DB; defaults to the app's learner store."""

    def __init__(self, session_factory: Callable[[], DbSession] = SessionLocal) -> None:
        self._session_factory = session_factory

    @contextmanager
    def _scope(self) -> Iterator[DbSession]:
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create_session(
        self, scenario_id: str, *, started_at: datetime | None = None
    ) -> int:
        """Open a Session row for a starting conversation and return its id. The
        graph stashes the id in state so debrief and any errors can reference it.
        `started_at` is injectable so a test can control the derived duration."""
        with self._scope() as db:
            session = Session(scenario=scenario_id)
            if started_at is not None:
                session.started_at = started_at
            db.add(session)
            db.flush()  # assigns the primary key without ending the transaction
            return session.id

    def end_session(
        self,
        session_id: int,
        *,
        ended_at: datetime | None = None,
        errors: Sequence[ErrorRow] = (),
    ) -> None:
        """Close the session: stamp ended_at, write the caught errors, and fold the
        elapsed minutes into the immersion metric as an app-source block. Missing
        session ids are a programming error, so they raise rather than pass."""
        with self._scope() as db:
            session = db.get(Session, session_id)
            if session is None:
                raise KeyError(f"Cannot end unknown session {session_id}.")
            session.mark_ended(ended_at)
            for error in errors:
                db.add(ErrorLog(session_id=session_id, **error))
            # duration_seconds is derived from the two timestamps; only count whole
            # minutes, and skip a zero so a blip of a session doesn't add a row.
            minutes = (session.duration_seconds or 0) // 60
            if minutes > 0:
                record_session_immersion(db, session_id, minutes)
