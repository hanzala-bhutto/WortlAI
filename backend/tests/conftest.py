"""Test environment.

Settings has no defaults for URLs and model ids, so the required values are set
here rather than depending on whatever .env exists on the machine running the
tests. Environment variables win over the .env file in pydantic-settings, so this
keeps the suite hermetic. Runs at import, before any test module imports app.main.
"""

import os

os.environ.setdefault("LANGFUSE_HOST", "http://localhost:3000")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("LLM_MODEL_PRIMARY", "openai/gpt-oss-120b")
os.environ.setdefault("LLM_MODEL_SECONDARY", "llama-3.3-70b-versatile")
os.environ.setdefault("STT_MODEL", "whisper-large-v3-turbo")
os.environ.setdefault("TTS_VOICE", "de-DE-KatjaNeural")
os.environ.setdefault("CORS_ORIGINS", '["http://localhost:3001"]')
