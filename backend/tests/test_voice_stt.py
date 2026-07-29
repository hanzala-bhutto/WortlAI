"""Contract for the Groq Whisper transcriber.

Exercised against an httpx2 MockTransport, so no network and no key: what matters
is that a good response yields clean German text, that the request is shaped the way
Groq's transcription API expects (multipart, model + German language), and above all
that every failure path becomes an STTError the voice loop can turn into a "didn't
catch that" notice rather than a hung or crashed session.
"""

from types import SimpleNamespace

import httpx2
import pytest

from app.llmops.tracing import Tracing
from app.voice.stt import GroqWhisper, STTError


def make_settings(*, groq_key="gk", max_bytes=2_000_000):
    return SimpleNamespace(
        groq_api_key=groq_key,
        groq_base_url="https://groq.test/v1",
        stt_model="whisper-large-v3-turbo",
        max_utterance_bytes=max_bytes,
    )


class FakeSpan:
    def __init__(self):
        self.updates = []

    def update(self, **kwargs):
        self.updates.append(kwargs)


class FakeCM:
    def __init__(self, span):
        self.span = span

    def __enter__(self):
        return self.span

    def __exit__(self, *_exc):
        return False


class FakeTracingClient:
    """Records every generation started, so tests can inspect what STT
    forwarded to Langfuse."""

    def __init__(self):
        self.spans: list[FakeSpan] = []
        self.starts: list[dict] = []

    def start_as_current_observation(self, **kwargs):
        self.starts.append(kwargs)
        span = FakeSpan()
        self.spans.append(span)
        return FakeCM(span)


def transcriber_with(handler, *, settings=None, tracing=None):
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return GroqWhisper(
        settings=settings or make_settings(), client=client, tracing=tracing
    )


async def test_transcribe_returns_trimmed_german_text():
    captured = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.content
        return httpx2.Response(200, json={"text": "  Guten Tag  "})

    text = await transcriber_with(handler).transcribe(
        b"opus-bytes", mimetype="audio/webm"
    )

    assert text == "Guten Tag"
    assert captured["url"].endswith("/audio/transcriptions")
    assert captured["auth"] == "Bearer gk"
    # Multipart body carries the model and pins German, so Whisper does not translate.
    assert b"whisper-large-v3-turbo" in captured["body"]
    assert b"language" in captured["body"] and b"de" in captured["body"]


async def test_http_error_becomes_stterror():
    with pytest.raises(STTError):
        await transcriber_with(
            lambda r: httpx2.Response(429, json={"e": "rate"})
        ).transcribe(b"a")


async def test_transport_error_becomes_stterror():
    def boom(request):
        raise httpx2.ConnectError("refused")

    with pytest.raises(STTError):
        await transcriber_with(boom).transcribe(b"a")


async def test_oversize_audio_is_rejected_before_the_network():
    calls: list[int] = []

    def handler(request):
        calls.append(1)
        return httpx2.Response(200, json={"text": "x"})

    t = transcriber_with(handler, settings=make_settings(max_bytes=8))
    with pytest.raises(STTError):
        await t.transcribe(b"far too many bytes")
    assert calls == []  # the cap fires before any request is made


async def test_empty_audio_is_rejected():
    with pytest.raises(STTError):
        await transcriber_with(
            lambda r: httpx2.Response(200, json={"text": "x"})
        ).transcribe(b"")


async def test_missing_key_is_stterror_not_a_live_401():
    t = transcriber_with(
        lambda r: httpx2.Response(200, json={"text": "x"}),
        settings=make_settings(groq_key=""),
    )
    with pytest.raises(STTError):
        await t.transcribe(b"audio")


async def test_malformed_body_is_stterror():
    with pytest.raises(STTError):
        await transcriber_with(
            lambda r: httpx2.Response(200, json={"nope": 1})
        ).transcribe(b"a")


async def test_prompt_is_sent_when_provided():
    captured = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["body"] = request.content
        return httpx2.Response(200, json={"text": "Einen Kaffee bitte"})

    await transcriber_with(handler).transcribe(
        b"opus-bytes", prompt="Ich nehme …, Einen Kaffee, bitte."
    )

    assert b"Ich nehme" in captured["body"]


async def test_prompt_is_omitted_when_not_provided():
    captured = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["body"] = request.content
        return httpx2.Response(200, json={"text": "x"})

    await transcriber_with(handler).transcribe(b"opus-bytes")

    assert b"prompt" not in captured["body"]


async def test_oversize_prompt_is_truncated_not_rejected():
    captured = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["body"] = request.content
        return httpx2.Response(200, json={"text": "x"})

    huge_prompt = "wort " * 500  # far past Groq's 224-token limit
    await transcriber_with(handler).transcribe(b"opus-bytes", prompt=huge_prompt)

    assert len(captured["body"]) < len(huge_prompt) + 500  # sanity: it was cut down


async def test_silence_transcribes_to_empty_string_not_an_error():
    # A silent utterance is a normal degraded state, not a failure: the loop prompts
    # the user to speak again rather than surfacing an error.
    text = await transcriber_with(
        lambda r: httpx2.Response(200, json={"text": "   "})
    ).transcribe(b"a")
    assert text == ""


async def test_success_records_a_span_with_metadata_and_output_not_raw_audio():
    tracing_client = FakeTracingClient()
    t = transcriber_with(
        lambda r: httpx2.Response(200, json={"text": "Guten Tag"}),
        tracing=Tracing(client=tracing_client),
    )

    text = await t.transcribe(b"opus-bytes", mimetype="audio/webm")

    assert text == "Guten Tag"
    [start] = tracing_client.starts
    assert start["name"] == "stt.transcribe"
    assert start["model"] == "whisper-large-v3-turbo"
    # Metadata only - byte count and mimetype - never the audio bytes themselves.
    assert start["input"] == {
        "audio_bytes": len(b"opus-bytes"),
        "mimetype": "audio/webm",
    }
    [span] = tracing_client.spans
    assert span.updates[-1]["output"] == "Guten Tag"


async def test_failed_transcription_still_records_a_span():
    tracing_client = FakeTracingClient()
    t = transcriber_with(
        lambda r: httpx2.Response(429, json={"e": "rate"}),
        tracing=Tracing(client=tracing_client),
    )

    with pytest.raises(STTError):
        await t.transcribe(b"a")

    # A gap-free timeline: the span exists even though the call failed, and it
    # is marked as an error rather than silently closing clean.
    [span] = tracing_client.spans
    assert span.updates[-1]["level"] == "ERROR"
