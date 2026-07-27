"""Speech to text: one utterance of German audio in, transcript out.

The only place that talks to Groq's Whisper endpoint. It is a *different* endpoint
from the chat provider (multipart `/audio/transcriptions`, not `/chat/completions`),
so it is its own small client rather than a method on LLMProvider - but it mirrors
the provider's shape: pass a `client` on an httpx2 MockTransport to make it
deterministic in tests, otherwise one is created lazily and owned here.

Batch, not streaming: the browser records a whole push-to-talk utterance and posts
the blob, so there is one request per turn (feasibility 003). Groq Whisper is ~200x
realtime, well inside the latency budget, and streaming STT is not on the free tier.

Failure is a normal path (guardrail #4): a transport error or a non-200 becomes an
STTError the caller turns into a "didn't catch that" notice, never a hung session.
A silent utterance transcribes to "" - that is not an error, it is returned as-is so
the caller can prompt the user to speak again.
"""

from __future__ import annotations

from typing import Protocol

import httpx2

from app.config import Settings, get_settings

# Whisper is multilingual; pinning German both improves accuracy on A2 speech and
# stops it "helpfully" translating a hesitant German utterance into English.
STT_LANGUAGE = "de"

# The blob the browser sends is webm/opus (Chrome MediaRecorder default). Sent as
# the multipart filename extension so Groq routes it to the right decoder.
_DEFAULT_MIMETYPE = "audio/webm"
_FILENAME = "utterance.webm"


class STTError(RuntimeError):
    """Transcription failed (transport, non-200, or an unreadable body). The caller
    degrades - prompts the user to repeat - rather than treating it as fatal."""


class Transcriber(Protocol):
    """The slice the voice loop needs: audio bytes -> transcript. Implemented by
    GroqWhisper in production and by a fake in tests, so the loop never needs a
    network or a real key to be exercised."""

    async def transcribe(self, audio: bytes, *, mimetype: str = ...) -> str: ...


class GroqWhisper:
    """Groq `whisper-large-v3-turbo` over the OpenAI-compatible transcription API."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx2.AsyncClient:
        if self._client is None:
            self._client = httpx2.AsyncClient(
                timeout=httpx2.Timeout(30.0, connect=5.0)
            )
        return self._client

    async def transcribe(self, audio: bytes, *, mimetype: str = _DEFAULT_MIMETYPE) -> str:
        settings = self._settings
        if not audio:
            raise STTError("empty audio")
        # Byte ceiling as a proxy for the audio-seconds cap (guardrail #3): we would
        # have to decode the container to know the true duration, and a size cap
        # stops abuse just as well without pulling in a decoder.
        if len(audio) > settings.max_utterance_bytes:
            raise STTError(
                f"utterance too large: {len(audio)} bytes > {settings.max_utterance_bytes}"
            )
        if not settings.groq_api_key:
            raise STTError("STT unavailable: GROQ_API_KEY is not set")

        url = f"{settings.groq_base_url}/audio/transcriptions"
        files = {"file": (_FILENAME, audio, mimetype)}
        data = {
            "model": settings.stt_model,
            "language": STT_LANGUAGE,
            "response_format": "json",
            "temperature": "0",
        }
        headers = {"Authorization": f"Bearer {settings.groq_api_key}"}

        client = self._get_client()
        try:
            response = await client.post(url, files=files, data=data, headers=headers)
            response.raise_for_status()
        except httpx2.HTTPStatusError as exc:
            raise STTError(f"HTTP {exc.response.status_code}") from exc
        except (httpx2.TimeoutException, httpx2.TransportError) as exc:
            raise STTError(f"transport error: {exc}") from exc

        try:
            text = response.json()["text"]
        except (ValueError, KeyError, TypeError) as exc:
            raise STTError(f"malformed transcription body: {exc}") from exc
        return text.strip()
