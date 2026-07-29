"""The voice pipeline: the STT and TTS pair the WebSocket route drives.

Assembled once at app startup and held on app.state next to the session runtime.
It owns the one thing with a lifecycle - the transcriber's HTTP client - and closes
it on shutdown. The synthesizer is stateless (edge-tts opens a connection per
sentence), so there is nothing to close there.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings, get_settings
from app.llmops.tracing import Tracing, build_tracing
from app.voice.stt import GroqWhisper, Transcriber
from app.voice.tts import EdgeTTS, Synthesizer


@dataclass
class VoicePipeline:
    transcriber: Transcriber
    synthesizer: Synthesizer

    async def aclose(self) -> None:
        close = getattr(self.transcriber, "aclose", None)
        if close is not None:
            await close()


def build_voice_pipeline(
    settings: Settings | None = None, tracing: Tracing | None = None
) -> VoicePipeline:
    settings = settings or get_settings()
    tracing = tracing or build_tracing()
    return VoicePipeline(
        transcriber=GroqWhisper(settings=settings, tracing=tracing),
        synthesizer=EdgeTTS(
            voice=settings.tts_voice,
            rate_min=settings.voice_rate_min,
            rate_max=settings.voice_rate_max,
        ),
    )
