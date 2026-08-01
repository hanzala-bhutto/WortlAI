"""Contract for app.rag.qdrant_store (#11): two Qdrant collections loaded and
queried through LlamaIndex.

Runs against qdrant-client's `:memory:` mode (an embedded, in-process Qdrant),
never a live docker instance, so these tests describe the wiring rather than
Qdrant's own behaviour. The acceptance criterion from issue #11 - "a filtered
semantic query returns relevant German content for a topic+level" - is the
main thing under test: a bag-of-words fake embedder (deterministic, no
network, no ~1.1GB download) stands in for e5/NIM so similarity actually
correlates with shared vocabulary, letting the tests assert *which* record
comes back, not just that *a* response comes back.
"""

import hashlib

import pytest
from qdrant_client import AsyncQdrantClient

from app.rag.glossary_parser import WordRecord
from app.rag.qdrant_store import index_records, query_filtered
from app.rag.vision_extract import RedemittelPhrase, RedemittelSet

_DIM = 32


class FakeEmbedder:
    """Bag-of-words hash embedding: each word maps to a fixed pseudo-random
    unit vector (seeded on the word itself), summed and renormalized. Query and
    passage modes are identical on purpose - the point is deterministic,
    content-correlated vectors, not asymmetry, which rag/embedder.py's real
    tests already cover."""

    dimension = _DIM

    async def embed(self, texts: list[str], mode: str) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    @staticmethod
    def _embed_one(text: str) -> list[float]:
        vec = [0.0] * _DIM
        for word in text.lower().split():
            digest = hashlib.sha256(word.encode()).digest()
            for i in range(_DIM):
                vec[i] += digest[i % len(digest)] / 255.0 - 0.5
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]


@pytest.fixture
async def client():
    c = AsyncQdrantClient(location=":memory:")
    yield c
    await c.close()


async def test_index_and_query_returns_matching_record(client):
    await index_records(
        "vocab",
        [
            {
                "text": "der Termin appointment",
                "metadata": {"level": "A2", "topic": "Arzt", "chapter": 3},
            },
            {
                "text": "das Wetter weather",
                "metadata": {"level": "A2", "topic": "Alltag", "chapter": 1},
            },
        ],
        client=client,
        embedder=FakeEmbedder(),
    )

    results = await query_filtered(
        "vocab",
        "Termin appointment",
        {"topic": "Arzt"},
        client=client,
        embedder=FakeEmbedder(),
    )

    assert len(results) == 1
    assert results[0].node.metadata["topic"] == "Arzt"
    assert "Termin" in results[0].node.text


async def test_filter_excludes_non_matching_level(client):
    await index_records(
        "content",
        [
            {
                "text": "einen Vorschlag machen",
                "metadata": {"level": "A2", "type": "redemittel"},
            },
            {
                "text": "Perfekt mit haben oder sein",
                "metadata": {"level": "B1", "type": "grammar"},
            },
        ],
        client=client,
        embedder=FakeEmbedder(),
    )

    results = await query_filtered(
        "content",
        "Vorschlag machen",
        {"level": "A2"},
        client=client,
        embedder=FakeEmbedder(),
        top_k=5,
    )

    assert all(r.node.metadata["level"] == "A2" for r in results)
    assert len(results) == 1


async def test_filter_with_list_value_uses_in_operator(client):
    await index_records(
        "vocab",
        [
            {"text": "der Bahnhof", "metadata": {"chapter": 1}},
            {"text": "die Kirche", "metadata": {"chapter": 2}},
            {"text": "das Rathaus", "metadata": {"chapter": 5}},
        ],
        client=client,
        embedder=FakeEmbedder(),
    )

    results = await query_filtered(
        "vocab",
        "Gebäude in der Stadt",
        {"chapter": [1, 2]},
        client=client,
        embedder=FakeEmbedder(),
        top_k=10,
    )

    chapters = {r.node.metadata["chapter"] for r in results}
    assert chapters == {1, 2}


async def test_records_with_blank_text_are_skipped(client):
    await index_records(
        "vocab",
        [
            {"text": "   ", "metadata": {}},
            {"text": "der Hund", "metadata": {"topic": "Tiere"}},
        ],
        client=client,
        embedder=FakeEmbedder(),
    )

    results = await query_filtered(
        "vocab", "Hund", {"topic": "Tiere"}, client=client, embedder=FakeEmbedder()
    )

    assert len(results) == 1


async def test_acceptance_filtered_query_over_real_word_and_redemittel_records(client):
    """The issue #11 acceptance criterion, against actual #9/#10 output shapes
    rather than ad-hoc dicts: a level+topic filtered query returns only the
    German content matching both, from a corpus mixing WordRecord (#9) and
    RedemittelSet (#10) records."""
    arzt_word = WordRecord(
        lemma="der Termin",
        lemma_raw="der Termin",
        pos="noun",
        article="der",
        translation_en="appointment",
        topic="Arzt",
        level="A1",
        source_page=12,
    )
    wetter_word = WordRecord(
        lemma="das Wetter",
        lemma_raw="das Wetter",
        pos="noun",
        article="das",
        translation_en="weather",
        topic="Alltag",
        level="A1",
        source_page=4,
    )
    arzt_redemittel = RedemittelSet(
        title="beim Arzt",
        phrases=[RedemittelPhrase(phrase="Ich habe einen Termin bei Ihnen.")],
        source_text="Ich habe einen Termin bei Ihnen.",
        source_page=40,
    )

    def to_record(*, text: str, topic: str, level: str) -> dict:
        return {"text": text, "metadata": {"topic": topic, "level": level}}

    await index_records(
        "vocab",
        [
            to_record(
                text=f"{arzt_word.lemma} {arzt_word.translation_en}",
                topic=arzt_word.topic,
                level=arzt_word.level,
            ),
            to_record(
                text=f"{wetter_word.lemma} {wetter_word.translation_en}",
                topic=wetter_word.topic,
                level=wetter_word.level,
            ),
        ],
        client=client,
        embedder=FakeEmbedder(),
    )
    await index_records(
        "content",
        [
            to_record(
                text=arzt_redemittel.phrases[0].phrase,
                topic="Arzt",
                level="A2",
            )
        ],
        client=client,
        embedder=FakeEmbedder(),
    )

    vocab_results = await query_filtered(
        "vocab",
        "Termin appointment",
        {"topic": "Arzt"},
        client=client,
        embedder=FakeEmbedder(),
    )
    content_results = await query_filtered(
        "content",
        "Termin beim Arzt",
        {"topic": "Arzt"},
        client=client,
        embedder=FakeEmbedder(),
    )

    assert len(vocab_results) == 1
    assert "Termin" in vocab_results[0].node.text
    assert len(content_results) == 1
    assert content_results[0].node.text == arzt_redemittel.phrases[0].phrase


async def test_index_records_with_no_survivors_is_a_no_op(client):
    # Must not raise (e.g. on an empty node list reaching Qdrant) and must not
    # create the collection.
    await index_records(
        "vocab",
        [{"text": "  ", "metadata": {}}],
        client=client,
        embedder=FakeEmbedder(),
    )

    assert await client.collection_exists("vocab") is False
