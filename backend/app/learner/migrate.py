"""Bring the learner DB to the latest schema.

Called once at startup so a fresh clone gets its tables on first run without a
manual `alembic upgrade`. Alembic is still the source of truth - this just runs
`upgrade head` in-process against the same config the CLI uses.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


def upgrade_to_head() -> None:
    config = Config(str(ALEMBIC_INI))
    # script_location is relative to the ini in the file, but the process may run
    # from anywhere (uvicorn from backend/, tests from the repo root); pin it.
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")
