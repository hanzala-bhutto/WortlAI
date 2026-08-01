"""Two Qdrant collections loaded via LlamaIndex (#11): `vocab` (#9's word
records) and `content` (#10's Redemittel/grammar boxes) - split by content
shape, not by tenant, since the two record types carry different fields.

Both collections share whichever embedder `rag/embedder.py` has selected
(docs/feasibility/011-embedder-qdrant-collections.md): local e5-large and
NIM's `nv-embedqa-e5-v5` are both 1024-dim, so a Qdrant collection's vector
size never depends on which provider is active, but a provider swap still
means re-ingesting into fresh collections - vectors from different models
aren't comparable under cosine similarity even at equal dimension.

`MetadataFilters` is LlamaIndex's store-agnostic filter API; the Qdrant
integration translates EQ/IN clauses into native Qdrant `Filter` objects, so
the acceptance criterion (a level+topic filtered query returns only matching
content) needs no custom Qdrant client code. Payload indexes
(`create_payload_index`) are deliberately not created: Qdrant's own docs frame
that as a large-corpus performance optimization, not a correctness
requirement, and this project's corpus is under 10k vectors - a scan-per-query
is sub-millisecond at this scale (docs/feasibility/011). Revisit if #12's
lexical-graph queries start compounding multiple filters per call.

Every operation here is async and uses Qdrant's `AsyncQdrantClient` only, never
a sync `QdrantClient` alongside it - `:memory:` mode spins up a separate,
non-shared instance per client object, so mixing sync and async clients
against the same collection would silently split reads from writes.
"""

import asyncio
from typing import Literal

from llama_index.core import VectorStoreIndex
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.schema import NodeWithScore, TextNode
from llama_index.core.vector_stores import (
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)
from llama_index.vector_stores.qdrant import QdrantVectorStore
from pydantic import PrivateAttr
from qdrant_client import AsyncQdrantClient

from app.config import Settings, get_settings
from app.rag.embedder import Embedder, get_embedder

CollectionName = Literal["vocab", "content"]


class _EmbedderAdapter(BaseEmbedding):
    """Bridges `rag/embedder.py`'s async, mode-based `Embedder` to LlamaIndex's
    `BaseEmbedding`, so a provider swap stays a rag/embedder.py-only change:
    LlamaIndex's own embedding classes are never the public interface other
    modules import (docs/feasibility/011's rejected alternative #1). Only the
    async methods are used by this module (see module docstring); the sync
    methods exist because `BaseEmbedding` is abstract, not because anything
    here calls them."""

    _embedder: Embedder = PrivateAttr()

    def __init__(self, embedder: Embedder) -> None:
        super().__init__()
        self._embedder = embedder

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return (await self._embedder.embed([query], mode="query"))[0]

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return (await self._embedder.embed([text], mode="passage"))[0]

    async def _aget_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return await self._embedder.embed(texts, mode="passage")

    def _get_query_embedding(self, query: str) -> list[float]:
        return asyncio.run(self._aget_query_embedding(query))

    def _get_text_embedding(self, text: str) -> list[float]:
        return asyncio.run(self._aget_text_embedding(text))


def get_qdrant_client(settings: Settings | None = None) -> AsyncQdrantClient:
    settings = settings or get_settings()
    return AsyncQdrantClient(url=settings.qdrant_url)


def _collection_name(settings: Settings, collection: CollectionName) -> str:
    return (
        settings.qdrant_collection_vocab
        if collection == "vocab"
        else settings.qdrant_collection_content
    )


def _index_for(
    collection: CollectionName,
    client: AsyncQdrantClient,
    embedder: Embedder,
    settings: Settings,
) -> VectorStoreIndex:
    vector_store = QdrantVectorStore(
        collection_name=_collection_name(settings, collection),
        aclient=client,
    )
    return VectorStoreIndex.from_vector_store(
        vector_store, embed_model=_EmbedderAdapter(embedder)
    )


async def index_records(
    collection: CollectionName,
    records: list[dict],
    *,
    client: AsyncQdrantClient,
    embedder: Embedder | None = None,
    settings: Settings | None = None,
) -> None:
    """Embed and write `records` into one collection. Each record is
    `{"text": str, "metadata": dict}`; `metadata` carries the payload filter
    fields (`level`/`topic`/`chapter`/`type`) the acceptance criterion queries
    on - the caller decides what a #9 `WordRecord` or #10
    `RedemittelSet`/`GrammarBox` maps to, this module only indexes what it's
    given. A record with no text is skipped rather than embedding an empty
    string."""
    settings = settings or get_settings()
    embedder = embedder or get_embedder(settings)
    nodes = [
        TextNode(text=r["text"], metadata=r.get("metadata", {}))
        for r in records
        if r.get("text", "").strip()
    ]
    if not nodes:
        return
    index = _index_for(collection, client, embedder, settings)
    await index.ainsert_nodes(nodes)


async def query_filtered(
    collection: CollectionName,
    query_text: str,
    filters: dict[str, str | list[str]],
    *,
    client: AsyncQdrantClient,
    embedder: Embedder | None = None,
    settings: Settings | None = None,
    top_k: int = 5,
) -> list[NodeWithScore]:
    """Filtered semantic query over one collection. `filters` maps a payload
    key to either a single value (equality) or a list (any of)."""
    settings = settings or get_settings()
    embedder = embedder or get_embedder(settings)
    index = _index_for(collection, client, embedder, settings)
    metadata_filters = MetadataFilters(
        filters=[
            MetadataFilter(
                key=key,
                value=value,
                operator=FilterOperator.IN
                if isinstance(value, list)
                else FilterOperator.EQ,
            )
            for key, value in filters.items()
        ]
    )
    retriever = index.as_retriever(similarity_top_k=top_k, filters=metadata_filters)
    return await retriever.aretrieve(query_text)
