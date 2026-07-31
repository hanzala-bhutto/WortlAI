"""Settings loaded from the repo-root .env. See .env.example for every value."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# app/config.py -> app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # API keys. Empty means the feature is unavailable, which /health reports;
    # it is not a startup error.
    groq_api_key: str = ""
    nim_api_key: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Everything below is required: no default, so a missing value fails at boot
    # rather than silently pointing somewhere plausible.
    langfuse_base_url: str
    qdrant_url: str

    # Single-user app (#51): every trace is tagged with this id so Langfuse's
    # Sessions view can filter by user, even though there's only one learner.
    langfuse_user_id: str = "hanzala"

    # OpenAI-compatible chat endpoints. Both providers speak the same schema, so
    # the provider layer is one client over these base URLs.
    groq_base_url: str
    nim_base_url: str

    # The fallback chain, in order: primary and secondary run on Groq, the last
    # on NVIDIA NIM. No defaults, so a typo fails at boot instead of at 2am.
    llm_model_primary: str
    llm_model_secondary: str
    llm_model_fallback: str
    # Vision-capable NIM model for document extraction (#10). Not part of the
    # text fallback chain above: Groq has no usable free-tier vision model
    # (docs/feasibility/010-vision-extraction.md), so this is a single link,
    # not a chain.
    nim_vision_model: str
    stt_model: str
    tts_voice: str

    cors_origins: list[str]

    data_dir: Path = BACKEND_DIR / "data"

    # Tutor tuning. Tunables with sane defaults (like data_dir), so a fresh clone
    # runs without setting them, but changing them never needs a code edit:
    # - which Langfuse prompt label the Tutor speaks with (flip to "staging" to
    #   trial a new system prompt without touching production),
    # - sampling temperature, and the per-reply token ceiling (guardrail #3, a cap
    #   so a runaway generation can't burn the free tier).
    tutor_prompt_label: str = "production"
    tutor_temperature: float = 0.6
    tutor_max_tokens: int = 320

    # Corrector tuning, mirroring the Tutor knobs. Temperature 0 because error
    # detection wants repeatability, not variety. The severity threshold is the
    # staged-correction policy (guardrail/pedagogy): "critical" surfaces only
    # communication-breaking errors in the early weeks; flip to "minor" later to
    # start debriefing the smaller slips too - a config change, not a code edit.
    corrector_prompt_label: str = "production"
    corrector_temperature: float = 0.0
    corrector_max_tokens: int = 512
    corrector_severity_threshold: str = "critical"

    # Voice loop guardrails (#3 / guardrail #3): a per-utterance byte ceiling so a
    # runaway or malicious client can't ship huge audio to the STT free tier, and a
    # per-connection turn cap so one session can't loop forever burning requests.
    max_utterance_bytes: int = 2_000_000
    voice_max_turns: int = 60

    # Playback speed for TTS. All three voice-speed knobs live here (not scattered
    # in code): the default when a client sends no rate, and the band a client rate
    # is clamped to. Slow for early practice, up to slightly-fast later.
    voice_rate_default: float = 1.0
    voice_rate_min: float = 0.7
    voice_rate_max: float = 1.2

    def configured_keys(self) -> dict[str, bool]:
        """Which credentials are present, so a misconfigured .env shows up before
        it fails mid-conversation."""
        return {
            "groq": bool(self.groq_api_key),
            "nim": bool(self.nim_api_key),
            "langfuse": bool(self.langfuse_public_key and self.langfuse_secret_key),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
