"""The one place that talks to an embedding model (#11).

Two providers, chosen at startup by `Settings.embedder_provider`, never mixed
mid-corpus: `LocalE5Embedder` runs `intfloat/multilingual-e5-large` on CPU via
LlamaIndex's `HuggingFaceEmbedding`, no key needed; `NIMEmbedder` calls NVIDIA
NIM's `/v1/embeddings` endpoint instead. Both are 1024-dimensional
(docs/feasibility/011-embedder-qdrant-collections.md, live-spiked 2026-07-31),
so a Qdrant collection's vector size never depends on which one is chosen -
but a provider swap still means re-ingesting from scratch, since vectors from
different models are not comparable under cosine similarity even at equal
dimension.

Both providers are asymmetric: a query embedding and a passage embedding for
the same text differ, so `mode` is part of the interface rather than left to
each caller to remember. e5 encodes this as a text prefix ("query: "/
"passage: ", via `HuggingFaceEmbedding`'s `query_instruction`/`text_instruction`
params - the model's own instructions, confirmed still enforced by the current
checkpoint); NIM encodes it as a request field (`input_type`). An interface
that only knew "prepend a string" would silently do the wrong thing, or
nothing, the moment NIM is selected - see docs/feasibility/011's "alternatives
considered".

A missing embedding is a hard failure (`EmbedderError`), not something to
retry across providers the way `llm/provider.py`'s chat completions fall back:
query and passage vectors must come from the same model or cosine similarity
is meaningless, so there is no per-request fallback chain here.
"""

import asyncio
from typing import Literal, Protocol

import httpx2

from app.config import Settings, get_settings

EmbedMode = Literal["query", "passage"]

# Spike-verified 2026-07-31 (docs/feasibility/011): NIM's embeddings endpoint
# 400s with a generic, misleading "error parsing the body" if `truncate` is
# missing - it does not say which field is wrong. Always sent, never a config
# knob: there is nothing to configure, the model just requires it.
_NIM_TRUNCATE = "NONE"


class EmbedderError(RuntimeError):
    """The configured embedder failed. No fallback chain: an embedding is
    either valid or the caller must treat it as unavailable."""


class Embedder(Protocol):
    """The shape both providers satisfy. `dimension` lets collection-creation
    code assert a Qdrant collection's vector size matches before writing."""

    dimension: int

    async def embed(self, texts: list[str], mode: EmbedMode) -> list[list[float]]: ...


class LocalE5Embedder:
    """`intfloat/multilingual-e5-large` via LlamaIndex's `HuggingFaceEmbedding`
    (sentence-transformers underneath), CPU, no API key. Model download and
    inference are blocking calls, run off the event loop via `asyncio.to_thread`
    so this coroutine never stalls anything else awaiting on it."""

    dimension = 1024

    def __init__(self, model_name: str, model: object | None = None) -> None:
        # `model` lets tests inject a fake in place of a real ~1.1GB HF download
        # (same escape hatch #10's tests use for the network-dependent parts).
        # The real HuggingFaceEmbedding is built lazily on first `embed()`, not
        # here - constructing this class (e.g. via get_embedder(), to check
        # which provider was selected) must never trigger a model download.
        self._model_name = model_name
        self._model = model

    def _get_model(self) -> object:
        if self._model is None:
            from llama_index.embeddings.huggingface import HuggingFaceEmbedding

            self._model = HuggingFaceEmbedding(
                model_name=self._model_name,
                query_instruction="query: ",
                text_instruction="passage: ",
            )
        return self._model

    async def embed(self, texts: list[str], mode: EmbedMode) -> list[list[float]]:
        model = self._get_model()
        if mode == "query":
            return await asyncio.to_thread(
                lambda: [model.get_query_embedding(t) for t in texts]
            )
        return await asyncio.to_thread(model.get_text_embedding_batch, texts)


class NIMEmbedder:
    """NVIDIA NIM's OpenAI-compatible `/v1/embeddings`, `nvidia/nv-embedqa-e5-v5`
    by default. A direct httpx2 call, not `llama-index-embeddings-nvidia`: that
    package's NVIDIAEmbedding does not expose the `truncate` field this model
    requires (docs/feasibility/011), so controlling the request body directly
    is more reliable than fighting a wrapper's defaults.

    Pass a `client` (e.g. on a MockTransport) to make calls deterministic in
    tests; otherwise one is created lazily and owned by this instance.
    """

    dimension = 1024

    def __init__(
        self,
        settings: Settings,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        if not settings.nim_api_key:
            raise EmbedderError("NIM is not configured: set NIM_API_KEY.")
        self._settings = settings
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx2.AsyncClient:
        if self._client is None:
            self._client = httpx2.AsyncClient(timeout=httpx2.Timeout(30.0, connect=5.0))
        return self._client

    async def embed(self, texts: list[str], mode: EmbedMode) -> list[list[float]]:
        url = f"{self._settings.nim_base_url}/embeddings"
        payload = {
            "input": texts,
            "model": self._settings.embedder_model_nim,
            "input_type": mode,
            "truncate": _NIM_TRUNCATE,
        }
        headers = {
            "Authorization": f"Bearer {self._settings.nim_api_key}",
            "Content-Type": "application/json",
        }
        client = self._get_client()
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
        except httpx2.HTTPStatusError as exc:
            raise EmbedderError(
                f"NIM embeddings failed: HTTP {exc.response.status_code}"
            ) from exc
        except (httpx2.TimeoutException, httpx2.TransportError) as exc:
            raise EmbedderError(f"NIM embeddings failed: {exc}") from exc

        try:
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            return [item["embedding"] for item in data]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise EmbedderError(f"malformed NIM embeddings response: {exc}") from exc


def get_embedder(settings: Settings | None = None) -> Embedder:
    """The one call site everything else in `rag/` depends on to get vectors,
    same shape as `llm/provider.py` being the one place that talks to an LLM."""
    settings = settings or get_settings()
    if settings.embedder_provider == "nim":
        return NIMEmbedder(settings)
    return LocalE5Embedder(settings.embedder_model_local)
