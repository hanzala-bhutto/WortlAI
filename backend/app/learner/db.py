"""Engine, session factory and the declarative base for the learner store.

One SQLite file, `data_dir/wortlai.db`, holds the app's own data: sessions, the
error log and immersion hours. LangGraph's checkpointer gets its own file later
(`checkpoints.db`) so the two writers never share a connection - see
docs/feasibility/007-persistence.md.

SQLite is single-user here, so the store is synchronous. Async callers (the voice
loop, the Corrector) push writes to a worker thread with `asyncio.to_thread`
rather than dragging an async driver through the whole stack.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings, get_settings

# Name every constraint deterministically. SQLite can't ALTER, so Alembic rebuilds
# tables in batch mode, and it can only drop or alter a constraint it can name.
# Anonymous foreign keys would make a future migration fail; naming them now, before
# the first migration ships, is the cheap moment to do it.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base every model inherits from. Alembic reads its metadata."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def database_url(settings: Settings) -> str:
    """Where the app DB lives. Derived from data_dir rather than an env var: it is
    a local file, not a service address that could point somewhere plausible and
    wrong."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{settings.data_dir / 'wortlai.db'}"


def make_engine(url: str) -> Engine:
    """An engine with WAL enabled and foreign keys enforced.

    SQLite ships with both off: WAL lets a reader and the writer coexist (the
    /progress skill reading while a session writes), and foreign keys are what
    make the error_logs -> sessions link actually mean something.
    """
    engine = create_engine(url, future=True)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


engine = make_engine(database_url(get_settings()))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transactional scope for non-request callers (agents, skills). Commits on
    success, rolls back on any exception, always closes."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency. The route commits explicitly; this only guarantees the
    session is closed. Overridable in tests via app.dependency_overrides."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
