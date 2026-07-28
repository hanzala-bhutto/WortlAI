"""Test environment.

Settings has no defaults for URLs and model ids, so the required values are set
here rather than depending on whatever .env exists on the machine running the
tests. Environment variables win over the .env file in pydantic-settings, so this
keeps the suite hermetic. Runs at import, before any test module imports app.main.
"""

import os

os.environ.setdefault("LANGFUSE_BASE_URL", "http://localhost:3000")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
os.environ.setdefault("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
os.environ.setdefault("LLM_MODEL_PRIMARY", "openai/gpt-oss-120b")
os.environ.setdefault("LLM_MODEL_SECONDARY", "llama-3.3-70b-versatile")
os.environ.setdefault("LLM_MODEL_FALLBACK", "meta/llama-3.3-70b-instruct")
os.environ.setdefault("STT_MODEL", "whisper-large-v3")
os.environ.setdefault("TTS_VOICE", "de-DE-KatjaNeural")
os.environ.setdefault("CORS_ORIGINS", '["http://localhost:3001"]')

# These imports pull in app modules that read Settings at import time, so they
# must run after the environment is populated above. The ordering is deliberate;
# noqa: E402 tells the linter this is not the usual "imports belong at the top"
# mistake. (The alternative, the pytest-env plugin, would keep imports at the top
# but adds a dependency to do what four lines already do.)
import pytest  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.learner.db import Base, make_engine  # noqa: E402


@pytest.fixture
def db_session(tmp_path):
    """A fresh, isolated learner DB per test.

    A real file (not :memory:) so the WAL pragma and foreign-key enforcement in
    make_engine are exercised exactly as they run in production. create_all
    stands in for `alembic upgrade`, which the migration test covers separately.
    """
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
