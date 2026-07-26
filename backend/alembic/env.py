"""Alembic environment. Online-only: SQLite is local, so there is no offline
SQL-emit use case to support.

The URL and target metadata both come from the app so migrations describe the
same schema the models do, against the same file the app opens.
"""

from alembic import context

# Importing the models registers every table on Base.metadata; without this,
# autogenerate sees an empty schema.
from app.config import get_settings
from app.learner import models  # noqa: F401
from app.learner.db import Base, database_url, make_engine

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = make_engine(database_url(get_settings()))
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite can't ALTER most things in place; batch mode rewrites the
            # table so future column changes actually apply.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported for the learner store.")

run_migrations_online()
