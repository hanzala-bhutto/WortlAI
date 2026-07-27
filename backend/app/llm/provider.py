"""The one place that talks to an LLM.

Groq and NVIDIA NIM both expose the OpenAI chat-completions schema, so a single
async client over a base URL and a model id covers the whole fallback chain:
Groq `primary` -> Groq `secondary` -> NIM `fallback` (models and URLs from
config). No LiteLLM, no vendor SDK - two providers do not justify another layer
between us and the errors (see docs/feasibility/002-llm-provider.md).

Behaviour that the rest of the app relies on:
- `complete` returns the full reply; `stream` yields tokens as they arrive.
- A 429 or 5xx is retried a couple of times on the same model with backoff
  (honouring Retry-After), then the whole model is abandoned and the next link
  in the chain is tried. A provider with no API key is dropped from the chain.
- Streaming only falls back before the first token. Once tokens have reached the
  caller we cannot un-say them, so a mid-stream failure propagates instead of
  silently restarting on another model and duplicating output.
- Every link failing raises ProviderError. Callers degrade; they never hang.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx2

from app.config import Settings, get_settings
from app.llmops.tracing import Tracing

logger = logging.getLogger(__name__)

# Transient upstream states worth retrying on the same model before falling back.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES = 2  # attempts after the first, per model
BACKOFF_BASE_SECONDS = 0.5


class ProviderError(RuntimeError):
    """Every provider in the chain failed. The caller must degrade, not retry."""


class _ModelFailed(Exception):
    """One model gave up (retries exhausted or a non-retryable error). Internal
    signal to move to the next link in the chain."""


@dataclass(frozen=True)
class _Target:
    """One link in the fallback chain: which provider, where, and as which model."""

    provider: str
    base_url: str
    api_key: str
    model: str


def _build_chain(settings: Settings) -> list[_Target]:
    """Primary and secondary on Groq, fallback on NIM - but only for providers
    that actually have a key, so an unconfigured provider is skipped rather than
    guaranteed to 401."""
    chain: list[_Target] = []
    if settings.groq_api_key:
        chain.append(
            _Target(
                "groq",
                settings.groq_base_url,
                settings.groq_api_key,
                settings.llm_model_primary,
            )
        )
        chain.append(
            _Target(
                "groq",
                settings.groq_base_url,
                settings.groq_api_key,
                settings.llm_model_secondary,
            )
        )
    if settings.nim_api_key:
        chain.append(
            _Target(
                "nim",
                settings.nim_base_url,
                settings.nim_api_key,
                settings.llm_model_fallback,
            )
        )
    return chain


class LLMProvider:
    """Async chat client over the Groq/NIM fallback chain.

    Pass a `client` (e.g. an httpx2.AsyncClient on a MockTransport) to make calls
    deterministic in tests; otherwise one is created lazily and owned by this
    instance. Reuse a single provider so the connection pool is shared.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx2.AsyncClient | None = None,
        tracing: Tracing | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None
        # Defaults to a disabled no-op; the app wires build_tracing() so every LLM
        # call is traced. Which model actually answered is recorded on success.
        self._tracing = tracing or Tracing(client=None)

    async def __aenter__(self) -> "LLMProvider":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx2.AsyncClient:
        if self._client is None:
            self._client = httpx2.AsyncClient(timeout=httpx2.Timeout(60.0, connect=5.0))
        return self._client

    def _chain(self) -> list[_Target]:
        chain = _build_chain(self._settings)
        if not chain:
            raise ProviderError(
                "No LLM provider configured: set GROQ_API_KEY or NIM_API_KEY."
            )
        return chain

    @staticmethod
    def _headers(target: _Target) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {target.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _payload(
        target: _Target,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
        *,
        stream: bool,
    ) -> dict:
        payload: dict = {
            "model": target.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

    async def _backoff(self, attempt: int, response: httpx2.Response | None) -> None:
        """Exponential backoff, but never shorter than a server-sent Retry-After."""
        delay = BACKOFF_BASE_SECONDS * (2**attempt)
        if response is not None:
            retry_after = response.headers.get("retry-after")
            if retry_after:
                with contextlib.suppress(ValueError):
                    delay = max(delay, float(retry_after))
        await asyncio.sleep(delay)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        """Full, non-streamed reply from the first model in the chain that answers."""
        last_error: Exception | None = None
        with self._tracing.generation(
            name="llm.complete", input=messages, metadata={"temperature": temperature}
        ) as gen:
            for target in self._chain():
                try:
                    reply = await self._complete_one(
                        target, messages, temperature, max_tokens
                    )
                except _ModelFailed as exc:
                    logger.warning(
                        "LLM %s/%s failed, falling back: %s",
                        target.provider,
                        target.model,
                        exc,
                    )
                    last_error = exc
                    continue
                gen.update(output=reply, model=target.model)
                return reply
            raise ProviderError("All LLM providers failed.") from last_error

    async def _complete_one(
        self,
        target: _Target,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
    ) -> str:
        url = f"{target.base_url}/chat/completions"
        payload = self._payload(target, messages, temperature, max_tokens, stream=False)
        client = self._get_client()

        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await client.post(
                    url, json=payload, headers=self._headers(target)
                )
                response.raise_for_status()
                return _extract_content(response)
            except httpx2.HTTPStatusError as exc:
                if (
                    exc.response.status_code in RETRYABLE_STATUS
                    and attempt < MAX_RETRIES
                ):
                    await self._backoff(attempt, exc.response)
                    continue
                raise _ModelFailed(f"HTTP {exc.response.status_code}") from exc
            except (httpx2.TimeoutException, httpx2.TransportError) as exc:
                if attempt < MAX_RETRIES:
                    await self._backoff(attempt, None)
                    continue
                raise _ModelFailed(str(exc)) from exc

        # Unreachable: the loop either returns or raises on its final attempt.
        raise _ModelFailed("retries exhausted")

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Yield reply tokens as they arrive. Falls back only before the first
        token; a failure after tokens have been emitted is raised, not retried."""
        last_error: Exception | None = None
        with self._tracing.generation(
            name="llm.stream", input=messages, metadata={"temperature": temperature}
        ) as gen:
            for target in self._chain():
                chunks: list[str] = []
                try:
                    async for token in self._stream_one(
                        target, messages, temperature, max_tokens
                    ):
                        chunks.append(token)
                        yield token
                except _ModelFailed as exc:
                    logger.warning(
                        "LLM %s/%s stream failed, falling back: %s",
                        target.provider,
                        target.model,
                        exc,
                    )
                    last_error = exc
                    continue
                gen.update(output="".join(chunks), model=target.model)
                return
            raise ProviderError("All LLM providers failed.") from last_error

    async def _stream_one(
        self,
        target: _Target,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
    ) -> AsyncIterator[str]:
        url = f"{target.base_url}/chat/completions"
        payload = self._payload(target, messages, temperature, max_tokens, stream=True)
        client = self._get_client()
        started = False  # once True, we own the output and cannot fall back

        for attempt in range(MAX_RETRIES + 1):
            try:
                async with client.stream(
                    "POST", url, json=payload, headers=self._headers(target)
                ) as response:
                    if (
                        response.status_code in RETRYABLE_STATUS
                        and attempt < MAX_RETRIES
                    ):
                        await response.aread()
                        await self._backoff(attempt, response)
                        continue
                    response.raise_for_status()
                    async for token in _iter_sse(response):
                        started = True
                        yield token
                    if not started:
                        # A 200 that streamed no content is a dead turn; fall back
                        # to the next model, which can still be tried since nothing
                        # was emitted.
                        raise _ModelFailed("empty stream")
                    return
            except httpx2.HTTPStatusError as exc:
                if started:
                    raise
                raise _ModelFailed(f"HTTP {exc.response.status_code}") from exc
            except (httpx2.TimeoutException, httpx2.TransportError) as exc:
                if started:
                    raise
                if attempt < MAX_RETRIES:
                    await self._backoff(attempt, None)
                    continue
                raise _ModelFailed(str(exc)) from exc


def _extract_content(response: httpx2.Response) -> str:
    """Pull the reply text out of a non-streamed completion. A 200 with an
    unexpected or empty body is treated as a model failure so the chain falls
    back, rather than raising past the fallback logic or returning a dead turn."""
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise _ModelFailed(f"malformed completion body: {exc}") from exc
    if not content:
        raise _ModelFailed("empty completion content")
    return content


async def _iter_sse(response: httpx2.Response) -> AsyncIterator[str]:
    """Pull `delta.content` out of an OpenAI-style `data:`-prefixed token stream,
    stopping at the [DONE] sentinel. Malformed lines are skipped, not fatal."""
    async for raw in response.aiter_lines():
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = chunk.get("choices")
        if not choices:
            continue
        token = choices[0].get("delta", {}).get("content")
        if token:
            yield token
