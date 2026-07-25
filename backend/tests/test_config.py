"""Config contract: values come from the environment, not from source."""

import inspect

import pytest
from pydantic import ValidationError

from app import config as config_module
from app.config import Settings

REQUIRED_ENV = {
    "QDRANT_URL": "http://localhost:6333",
    "LANGFUSE_HOST": "http://localhost:3000",
    "GROQ_BASE_URL": "https://api.groq.com/openai/v1",
    "NIM_BASE_URL": "https://integrate.api.nvidia.com/v1",
    "LLM_MODEL_PRIMARY": "openai/gpt-oss-120b",
    "LLM_MODEL_SECONDARY": "llama-3.3-70b-versatile",
    "LLM_MODEL_FALLBACK": "meta/llama-3.3-70b-instruct",
    "STT_MODEL": "whisper-large-v3-turbo",
    "TTS_VOICE": "de-DE-KatjaNeural",
    "CORS_ORIGINS": '["http://localhost:3001"]',
}


@pytest.fixture
def env(monkeypatch):
    """A fully populated environment. Every Settings(...) call below passes
    _env_file=None so the tests describe the contract rather than whatever
    happens to be in this machine's .env."""
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    return monkeypatch


def test_settings_read_every_tunable_from_the_environment(env):
    env.setenv("QDRANT_URL", "http://qdrant.internal:6333")
    env.setenv("LLM_MODEL_PRIMARY", "some/other-model")
    env.setenv("TTS_VOICE", "de-DE-ConradNeural")

    settings = Settings(_env_file=None)

    assert settings.qdrant_url == "http://qdrant.internal:6333"
    assert settings.llm_model_primary == "some/other-model"
    assert settings.tts_voice == "de-DE-ConradNeural"


@pytest.mark.parametrize("missing", sorted(REQUIRED_ENV))
def test_missing_tunable_fails_loudly_at_boot(env, missing):
    """No silent fallback: a missing URL or model id must not quietly default to
    something plausible, or you end up debugging the wrong Qdrant."""
    env.delenv(missing)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)

    assert missing.lower() in str(excinfo.value).lower()


def test_absent_api_keys_are_degraded_not_fatal(env):
    """Keys are the deliberate exception to the no-defaults rule: a missing key is
    a state /health reports, not a crash."""
    for key in ("GROQ_API_KEY", "NIM_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        env.delenv(key, raising=False)

    settings = Settings(_env_file=None)

    assert settings.configured_keys() == {
        "groq": False,
        "nim": False,
        "langfuse": False,
    }


def test_langfuse_counts_as_configured_only_with_both_keys(env):
    env.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    env.delenv("LANGFUSE_SECRET_KEY", raising=False)

    assert Settings(_env_file=None).configured_keys()["langfuse"] is False


def test_no_urls_or_model_ids_are_hardcoded_in_source():
    """The rule from CLAUDE.md, enforced: tunables live in .env.example, not here."""
    source = inspect.getsource(config_module)

    assert "http://" not in source
    assert "https://" not in source
    # Model ids all contain a slash or a version-ish dash; the two we ship by
    # name must not appear as literals.
    assert "gpt-oss" not in source
    assert "whisper" not in source
    assert "Neural" not in source
