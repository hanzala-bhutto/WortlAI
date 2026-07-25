"""Contract for the LLM provider layer.

Everything is exercised against a mocked OpenAI-compatible endpoint (httpx
MockTransport), so these tests never touch the network and describe behaviour,
not Groq's live responses. The two things that matter and are hard to eyeball in
production are the fallback chain (429/5xx walks primary -> secondary -> NIM) and
that retries happen before a fallback, not instead of it.
"""

import json
from types import SimpleNamespace

import httpx
import pytest

from app.llm.provider import LLMProvider, ProviderError

PRIMARY = "openai/gpt-oss-120b"
SECONDARY = "llama-3.3-70b-versatile"
FALLBACK = "meta/llama-3.3-70b-instruct"


def make_settings(*, groq_key="gk", nim_key="nk"):
    """A settings stand-in with only the fields the provider reads. Keys default
    present; pass "" to simulate an unconfigured provider."""
    return SimpleNamespace(
        groq_api_key=groq_key,
        nim_api_key=nim_key,
        groq_base_url="https://groq.test/v1",
        nim_base_url="https://nim.test/v1",
        llm_model_primary=PRIMARY,
        llm_model_secondary=SECONDARY,
        llm_model_fallback=FALLBACK,
    )


def chat_response(content: str) -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    )


def sse_response(tokens: list[str]) -> httpx.Response:
    """A streamed chat completion, one delta per token plus the [DONE] sentinel."""
    lines = []
    for tok in tokens:
        payload = {"choices": [{"delta": {"content": tok}}]}
        lines.append(f"data: {json.dumps(payload)}\n\n")
    lines.append("data: [DONE]\n\n")
    return httpx.Response(200, text="".join(lines))


def provider_with(routes, *, settings=None):
    """Wire an LLMProvider to a MockTransport driven by `routes`: model id ->
    list of httpx.Response, consumed one per request (last repeats). Returns the
    provider and a list that records the model of every request made, in order."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        calls.append(model)
        queue = routes[model]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = LLMProvider(settings=settings or make_settings(), client=client)
    return provider, calls


MESSAGES = [{"role": "user", "content": "Hallo"}]


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Backoff waits must not slow the suite; assert they were requested, don't
    actually sleep."""
    async def instant(_seconds):
        return None

    monkeypatch.setattr("app.llm.provider.asyncio.sleep", instant)


async def test_complete_returns_primary_content_without_touching_fallback():
    provider, calls = provider_with({PRIMARY: [chat_response("Guten Tag")]})

    reply = await provider.complete(MESSAGES)

    assert reply == "Guten Tag"
    assert calls == [PRIMARY]


async def test_429_on_primary_falls_back_through_the_chain():
    provider, calls = provider_with(
        {
            PRIMARY: [httpx.Response(429, json={"error": "rate limited"})],
            SECONDARY: [httpx.Response(429, json={"error": "rate limited"})],
            FALLBACK: [chat_response("Vom NIM")],
        }
    )

    reply = await provider.complete(MESSAGES)

    assert reply == "Vom NIM"
    # Primary and secondary each retried (2 tries) before moving on; NIM answered.
    assert calls[-1] == FALLBACK
    assert calls.count(PRIMARY) >= 2
    assert calls.count(SECONDARY) >= 2


async def test_5xx_triggers_fallback_too():
    provider, calls = provider_with(
        {
            PRIMARY: [httpx.Response(503)],
            SECONDARY: [chat_response("Zweitmodell")],
        }
    )

    reply = await provider.complete(MESSAGES)

    assert reply == "Zweitmodell"
    assert SECONDARY in calls


async def test_a_single_429_is_retried_then_succeeds_on_the_same_model():
    provider, calls = provider_with(
        {PRIMARY: [httpx.Response(429), chat_response("Nach Retry")]}
    )

    reply = await provider.complete(MESSAGES)

    assert reply == "Nach Retry"
    assert calls == [PRIMARY, PRIMARY]


async def test_all_providers_failing_raises_provider_error():
    provider, _ = provider_with(
        {
            PRIMARY: [httpx.Response(500)],
            SECONDARY: [httpx.Response(500)],
            FALLBACK: [httpx.Response(500)],
        }
    )

    with pytest.raises(ProviderError):
        await provider.complete(MESSAGES)


async def test_no_configured_provider_raises_rather_than_hanging():
    provider, _ = provider_with(
        {}, settings=make_settings(groq_key="", nim_key="")
    )

    with pytest.raises(ProviderError):
        await provider.complete(MESSAGES)


async def test_an_unconfigured_provider_is_dropped_from_the_chain():
    """NIM has no key, so the chain is Groq-only and NIM is never called even
    when both Groq models fail."""
    provider, calls = provider_with(
        {
            PRIMARY: [httpx.Response(500)],
            SECONDARY: [httpx.Response(500)],
        },
        settings=make_settings(nim_key=""),
    )

    with pytest.raises(ProviderError):
        await provider.complete(MESSAGES)

    assert FALLBACK not in calls


async def test_malformed_200_body_falls_back_instead_of_raising():
    """A 200 with an unexpected shape must degrade like any other failure, not
    throw a KeyError past the fallback chain."""
    provider, calls = provider_with(
        {
            PRIMARY: [httpx.Response(200, json={"unexpected": "shape"})],
            SECONDARY: [chat_response("Gerettet")],
        }
    )

    reply = await provider.complete(MESSAGES)

    assert reply == "Gerettet"
    assert calls[-1] == SECONDARY


async def test_empty_content_falls_back_to_the_next_model():
    """gpt-oss occasionally returns an empty reply; a dead turn should hand off
    to the next model rather than surface as a blank string."""
    provider, calls = provider_with(
        {
            PRIMARY: [chat_response("")],
            SECONDARY: [chat_response("Etwas Echtes")],
        }
    )

    reply = await provider.complete(MESSAGES)

    assert reply == "Etwas Echtes"
    assert calls[-1] == SECONDARY


async def test_all_models_empty_raises_provider_error():
    provider, _ = provider_with(
        {
            PRIMARY: [chat_response("")],
            SECONDARY: [chat_response("")],
            FALLBACK: [chat_response("")],
        }
    )

    with pytest.raises(ProviderError):
        await provider.complete(MESSAGES)


async def test_stream_yields_tokens_in_order():
    provider, calls = provider_with({PRIMARY: [sse_response(["Ich", " bin", " hier"])]})

    tokens = [tok async for tok in provider.stream(MESSAGES)]

    assert "".join(tokens) == "Ich bin hier"
    assert calls == [PRIMARY]


async def test_stream_falls_back_before_the_first_token():
    provider, calls = provider_with(
        {
            PRIMARY: [httpx.Response(503)],
            SECONDARY: [sse_response(["Fall", "back"])],
        }
    )

    tokens = [tok async for tok in provider.stream(MESSAGES)]

    assert "".join(tokens) == "Fallback"
    assert calls[-1] == SECONDARY


async def test_stream_with_no_tokens_falls_back():
    """A 200 stream that carries only [DONE] and no content is a dead turn; the
    next model gets a chance since nothing was emitted yet."""
    provider, calls = provider_with(
        {
            PRIMARY: [sse_response([])],
            SECONDARY: [sse_response(["Da", "nn", " halt"])],
        }
    )

    tokens = [tok async for tok in provider.stream(MESSAGES)]

    assert "".join(tokens) == "Dann halt"
    assert calls[-1] == SECONDARY
