"""Contract for app.rag.embedder (#11).

`NIMEmbedder` is exercised against a mocked NIM endpoint (httpx2 MockTransport)
per docs/feasibility/011: the model requires `input_type` and `truncate` on
every request or it 400s, so these tests assert both are always sent, not just
that a happy-path call works. `LocalE5Embedder` is exercised against a fake
model object (no ~1.1GB HuggingFace download in the default test run) to
assert the query/passage instruction split and mode routing are correct - the
one thing e5 silently gets wrong if misconfigured, per the model card.
"""

import json
from types import SimpleNamespace

import httpx2
import pytest

from app.rag.embedder import (
    EmbedderError,
    LocalE5Embedder,
    NIMEmbedder,
    get_embedder,
)

NIM_MODEL = "nvidia/nv-embedqa-e5-v5"


def make_settings(*, provider="local", nim_key="nk"):
    return SimpleNamespace(
        embedder_provider=provider,
        embedder_model_local="intfloat/multilingual-e5-large",
        embedder_model_nim=NIM_MODEL,
        nim_api_key=nim_key,
        nim_base_url="https://nim.test/v1",
    )


def embeddings_response(vectors: list[list[float]]) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "object": "list",
            "data": [
                {"index": i, "embedding": v, "object": "embedding"}
                for i, v in enumerate(vectors)
            ],
            "model": NIM_MODEL,
            "usage": {"prompt_tokens": 3, "total_tokens": 3},
        },
    )


class FakeLocalModel:
    """Stands in for LlamaIndex's HuggingFaceEmbedding: records what it was
    asked for instead of running real inference."""

    def __init__(self):
        self.query_calls: list[str] = []
        self.batch_calls: list[list[str]] = []

    def get_query_embedding(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return [0.1, 0.2]

    def get_text_embedding_batch(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls.append(texts)
        return [[0.3, 0.4] for _ in texts]


# --- LocalE5Embedder ---------------------------------------------------------


async def test_local_query_mode_uses_get_query_embedding_per_text():
    model = FakeLocalModel()
    embedder = LocalE5Embedder("intfloat/multilingual-e5-large", model=model)

    result = await embedder.embed(["Wo ist der Bahnhof?"], mode="query")

    assert result == [[0.1, 0.2]]
    assert model.query_calls == ["Wo ist der Bahnhof?"]
    assert model.batch_calls == []


async def test_local_passage_mode_uses_batch_text_embedding():
    model = FakeLocalModel()
    embedder = LocalE5Embedder("intfloat/multilingual-e5-large", model=model)

    result = await embedder.embed(["Satz eins.", "Satz zwei."], mode="passage")

    assert result == [[0.3, 0.4], [0.3, 0.4]]
    assert model.batch_calls == [["Satz eins.", "Satz zwei."]]
    assert model.query_calls == []


def test_local_dimension_is_1024():
    assert LocalE5Embedder.dimension == 1024


# --- NIMEmbedder --------------------------------------------------------------


def nim_embedder_with(handler):
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return NIMEmbedder(make_settings(provider="nim"), client=client)


async def test_nim_embed_sends_input_type_and_truncate():
    captured = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.update(json.loads(request.content))
        return embeddings_response([[0.5] * 4])

    embedder = nim_embedder_with(handler)

    await embedder.embed(["Ich hätte gern einen Termin."], mode="query")

    assert captured["input_type"] == "query"
    assert captured["truncate"] == "NONE"
    assert captured["model"] == NIM_MODEL


async def test_nim_embed_passage_mode_sets_input_type_passage():
    captured = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.update(json.loads(request.content))
        return embeddings_response([[0.1] * 4])

    embedder = nim_embedder_with(handler)

    await embedder.embed(["ein Beispieltext"], mode="passage")

    assert captured["input_type"] == "passage"


async def test_nim_embed_returns_vectors_sorted_by_index():
    # Deliberately out of order, mirroring a provider that doesn't guarantee
    # response ordering matches request ordering.
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [2.0]},
                    {"index": 0, "embedding": [1.0]},
                ]
            },
        )

    embedder = nim_embedder_with(handler)

    result = await embedder.embed(["a", "b"], mode="query")

    assert result == [[1.0], [2.0]]


async def test_nim_embed_http_error_raises_embedder_error():
    embedder = nim_embedder_with(lambda _r: httpx2.Response(500))

    with pytest.raises(EmbedderError):
        await embedder.embed(["a"], mode="query")


async def test_nim_embed_malformed_body_raises_embedder_error():
    embedder = nim_embedder_with(lambda _r: httpx2.Response(200, json={"oops": True}))

    with pytest.raises(EmbedderError):
        await embedder.embed(["a"], mode="query")


def test_nim_embedder_requires_api_key():
    with pytest.raises(EmbedderError):
        NIMEmbedder(make_settings(provider="nim", nim_key=""))


def test_nim_dimension_is_1024():
    assert NIMEmbedder.dimension == 1024


# --- get_embedder factory -----------------------------------------------------


def test_get_embedder_defaults_to_local():
    embedder = get_embedder(make_settings(provider="local"))

    assert isinstance(embedder, LocalE5Embedder)


def test_get_embedder_selects_nim():
    embedder = get_embedder(make_settings(provider="nim"))

    assert isinstance(embedder, NIMEmbedder)
