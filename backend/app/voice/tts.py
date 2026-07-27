"""Text to speech: German text in, audio bytes out, one sentence at a time.

The only place that talks to edge-tts. edge-tts drives Microsoft's neural voices
through an *unofficial* endpoint (feasibility 003), so it is quarantined behind the
Synthesizer interface with Qwen3-TTS as the documented fallback: when edge-tts
breaks, a second implementation slots in without the voice loop noticing.

Sentence granularity is the point. The loop feeds each finished sentence here as
soon as the Tutor's reply produces it, so the first sentence's audio can start
playing while later sentences are still being synthesised - that is what keeps time
to first audio under the budget.

`edge_tts` is imported lazily inside the default factory so this module (and every
test that injects a fake Synthesizer) imports without the package installed.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from typing import Protocol

# Default playback-speed band, used when no bounds are supplied. The operator tunes
# the real band via Settings (voice_rate_min / voice_rate_max), passed in at
# construction; these constants are only the sane defaults. Requests outside the
# band are clamped, not rejected, so a bad client value degrades to the nearest
# sane speed.
MIN_RATE = 0.7
MAX_RATE = 1.2

# edge-tts emits MP3.
AUDIO_MIMETYPE = "audio/mpeg"

# A sentence runs up to and including its terminator (and any trailing space). The
# final fragment with no terminator is still a sentence, so nothing is dropped.
_SENTENCE = re.compile(r"\S.*?(?:[.!?…]+(?=\s|$)|$)", re.DOTALL)


class TTSError(RuntimeError):
    """Synthesis failed. The caller drops the audio for this sentence rather than
    hanging the turn; the learner still has the text transcript."""


def split_sentences(text: str) -> list[str]:
    """Break German text into sentences for per-sentence synthesis. Terminators
    are kept; whitespace is trimmed; empty pieces are dropped."""
    return [m.group().strip() for m in _SENTENCE.finditer(text) if m.group().strip()]


def rate_to_percent(
    rate: float, *, rate_min: float = MIN_RATE, rate_max: float = MAX_RATE
) -> str:
    """Map a playback multiplier (1.0 = normal) to edge-tts's relative-percent form,
    clamped to [rate_min, rate_max]. 1.0 -> '+0%', 0.7 -> '-30%', 1.2 -> '+20%'."""
    clamped = max(rate_min, min(rate_max, rate))
    percent = round((clamped - 1.0) * 100)
    return f"+{percent}%" if percent >= 0 else f"{percent}%"


class Synthesizer(Protocol):
    """The slice the voice loop needs: one sentence -> a stream of audio chunks.
    Async-iterable so a long sentence can start playing before it is fully rendered."""

    def synth(self, text: str, *, rate: float = ...) -> AsyncIterator[bytes]: ...


class EdgeTTS:
    """edge-tts neural German voice. `communicate_factory` is injectable so tests
    drive synthesis without touching the network; production uses edge_tts."""

    def __init__(
        self,
        voice: str,
        *,
        rate_min: float = MIN_RATE,
        rate_max: float = MAX_RATE,
        communicate_factory: Callable[..., object] | None = None,
    ) -> None:
        self._voice = voice
        self._rate_min = rate_min
        self._rate_max = rate_max
        self._factory = communicate_factory or _default_communicate

    async def synth(self, text: str, *, rate: float = 1.0) -> AsyncIterator[bytes]:
        if not text.strip():
            return
        percent = rate_to_percent(rate, rate_min=self._rate_min, rate_max=self._rate_max)
        comm = self._factory(text=text, voice=self._voice, rate=percent)
        try:
            async for chunk in comm.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    yield chunk["data"]
        except Exception as exc:  # edge-tts raises a grab-bag of network errors
            raise TTSError(f"synthesis failed: {exc}") from exc


def _default_communicate(*, text: str, voice: str, rate: str):
    """Build a real edge_tts.Communicate. Imported here so the package is only
    needed when actually synthesising, not to import this module."""
    import edge_tts

    return edge_tts.Communicate(text, voice, rate=rate)
