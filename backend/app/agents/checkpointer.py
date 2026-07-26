"""The LangGraph checkpointer: where a conversation's state is persisted so it
survives a backend restart (acceptance criterion #1 of #4).

Deliberately its own SQLite file, checkpoints.db, next to but separate from the
learner store's wortlai.db. They have different writers (the graph runtime vs our
SQLAlchemy code) and different lifecycles, so they never share a connection - see
docs/feasibility/007-persistence.md.

The saver holds one long-lived aiosqlite connection for the life of the process
(opened in the app's lifespan, closed on shutdown), rather than the per-call
`from_conn_string` context manager, because the graph is compiled once and reused
across every turn of every session.
"""

from __future__ import annotations

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import Settings, get_settings

CHECKPOINTS_FILENAME = "checkpoints.db"


def checkpoints_path(settings: Settings | None = None) -> str:
    """Where the checkpoint DB lives: alongside the learner store under data_dir."""
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return str(settings.data_dir / CHECKPOINTS_FILENAME)


async def open_checkpointer(
    path: str,
) -> tuple[AsyncSqliteSaver, aiosqlite.Connection]:
    """Open a checkpointer over `path` and return it with its connection, so the
    caller can close the connection on shutdown. `setup()` creates the checkpoint
    tables on first use and is a no-op afterwards."""
    conn = await aiosqlite.connect(path)
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    return saver, conn
