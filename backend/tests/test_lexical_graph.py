"""The lexical graph: word nodes, deterministic family edges, and the citation
gate that keeps hallucinated LLM edges out of the store.

The behaviours that matter: a re-ingest updates words in place, a verb's family
comes back from a walk, and an LLM edge only survives if its citation is real
text that actually names both words. The gate is tested through its drop paths,
not just the happy one - an uncited edge, a citation the model invented, a
citation that mentions neither word, and a malformed GOVERNS marker must all be
rejected.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.learner.models import Word, WordLink
from app.llmops.prompts import PromptStore
from app.rag.glossary_parser import VerbForm, WordRecord
from app.rag.lexical_graph import (
    Lemmatizer,
    citation_is_valid,
    derive_family_edges,
    extract_relational_edges,
    persist_relational_edges,
    persist_words,
    word_family,
)


def _verb(lemma, infinitive, prefix=None, *, page=1):
    return WordRecord(
        lemma=lemma,
        lemma_raw=lemma,
        pos="verb",
        verb=VerbForm(prefix=prefix, infinitive=infinitive),
        translation_en="(test)",
        source_page=page,
    )


def _noun(lemma, article, *, translation="(test)", topic=None, page=1):
    return WordRecord(
        lemma=lemma,
        lemma_raw=f"{article} {lemma}",
        pos="noun",
        article=article,
        translation_en=translation,
        topic=topic,
        source_page=page,
    )


# --- word nodes -----------------------------------------------------------------


def test_persist_words_writes_nodes_with_structural_fields(db_session):
    persist_words(db_session, [_noun("Messe", "die", topic="Arbeit")])
    db_session.commit()

    word = db_session.scalar(select(Word))
    assert word.lemma == "Messe"
    assert word.article == "die"
    assert word.topic == "Arbeit"
    assert word.source == "glossary"


def test_persist_words_is_idempotent_on_reingest(db_session):
    persist_words(db_session, [_noun("Messe", "die", translation="fair")])
    db_session.commit()
    # same natural key, changed payload -> update in place, not a duplicate row
    persist_words(db_session, [_noun("Messe", "die", translation="trade fair")])
    db_session.commit()

    rows = db_session.scalars(select(Word)).all()
    assert len(rows) == 1
    assert rows[0].translation_en == "trade fair"


# --- deterministic IN_FAMILY ----------------------------------------------------


def test_family_edges_link_base_and_separable_verbs(db_session):
    persist_words(
        db_session,
        [
            _verb("sprechen", "sprechen"),
            _verb("mit|sprechen", "sprechen", prefix="mit"),
            _verb("aus|sprechen", "sprechen", prefix="aus"),
            _verb("gehen", "gehen"),  # different family, must not link in
        ],
    )
    db_session.commit()

    derive_family_edges(db_session)
    db_session.commit()

    family = {w.lemma for w in word_family(db_session, "sprechen")}
    assert family == {"mit|sprechen", "aus|sprechen"}
    assert word_family(db_session, "gehen") == []


def test_family_edges_are_deterministic_and_need_no_citation(db_session):
    persist_words(
        db_session,
        [_verb("kommen", "kommen"), _verb("an|kommen", "kommen", prefix="an")],
    )
    db_session.commit()
    derive_family_edges(db_session)
    db_session.commit()

    edge = db_session.scalar(select(WordLink))
    assert edge.source == "deterministic"
    assert edge.citation is None


def test_family_derivation_is_idempotent(db_session):
    persist_words(
        db_session,
        [_verb("kommen", "kommen"), _verb("an|kommen", "kommen", prefix="an")],
    )
    db_session.commit()

    first = derive_family_edges(db_session)
    db_session.commit()
    second = derive_family_edges(db_session)
    db_session.commit()

    assert first == 2  # both directions of the one pair
    assert second == 0  # nothing new on a rerun
    assert db_session.scalar(select(func.count()).select_from(WordLink)) == 2


# --- the citation gate ----------------------------------------------------------

_CORPUS = "Ich freue mich auf das Wochenende. Der Chef ist nicht der Mitarbeiter."


def _persist_pair(db):
    persist_words(
        db,
        [
            _noun("Chef", "der", translation="boss"),
            _noun("Mitarbeiter", "der", translation="employee"),
            _verb("freuen", "freuen"),
            _noun("Wochenende", "das", translation="weekend"),
        ],
    )
    db.commit()


def test_valid_synonym_edge_survives_and_gets_its_reverse(db_session):
    _persist_pair(db_session)
    cand = {
        "from_lemma": "Chef",
        "to_lemma": "Mitarbeiter",
        "edge_type": "ANTONYM",
        "citation": "Der Chef ist nicht der Mitarbeiter.",
    }

    persist_relational_edges(db_session, [cand], _CORPUS)
    db_session.commit()

    edges = db_session.scalars(select(WordLink)).all()
    assert len(edges) == 2  # symmetric -> both directions
    assert all(e.source == "llm" and e.citation for e in edges)


def test_edge_with_invented_citation_is_dropped(db_session):
    _persist_pair(db_session)
    # A fluent-sounding sentence that never appears in the corpus.
    cand = {
        "from_lemma": "Chef",
        "to_lemma": "Mitarbeiter",
        "edge_type": "ANTONYM",
        "citation": "Der Chef mag den Mitarbeiter nicht.",
    }

    persist_relational_edges(db_session, [cand], _CORPUS)
    db_session.commit()

    assert db_session.scalar(select(func.count()).select_from(WordLink)) == 0


def test_edge_whose_citation_names_neither_word_is_dropped(db_session):
    _persist_pair(db_session)
    cand = {
        "from_lemma": "Chef",
        "to_lemma": "Mitarbeiter",
        "edge_type": "COLLOCATES_WITH",
        "citation": "Ich freue mich auf das Wochenende.",  # real span, wrong words
    }

    persist_relational_edges(db_session, [cand], _CORPUS)
    db_session.commit()

    assert db_session.scalar(select(func.count()).select_from(WordLink)) == 0


def test_governs_edge_needs_wellformed_prep_case(db_session):
    _persist_pair(db_session)
    good = {
        "from_lemma": "freuen",
        "to_lemma": "Wochenende",
        "edge_type": "GOVERNS",
        "citation": "Ich freue mich auf das Wochenende.",
        "detail": "auf+Akk",
    }
    malformed = {**good, "detail": "auf the accusative"}

    kept = persist_relational_edges(db_session, [malformed], _CORPUS)
    db_session.commit()
    assert kept == 0

    persist_relational_edges(db_session, [good], _CORPUS)
    db_session.commit()
    edge = db_session.scalar(select(WordLink).where(WordLink.edge_type == "GOVERNS"))
    assert edge.detail == "auf+Akk"


def test_governs_detail_accepts_full_case_name_and_canonicalizes(db_session):
    # The model often emits the full case ("auf+Akkusativ"); it must be kept and
    # stored canonically as "auf+Akk", not dropped on a format mismatch.
    _persist_pair(db_session)
    cand = {
        "from_lemma": "freuen",
        "to_lemma": "Wochenende",
        "edge_type": "GOVERNS",
        "citation": "Ich freue mich auf das Wochenende.",
        "detail": "auf+Akkusativ",
    }

    assert persist_relational_edges(db_session, [cand], _CORPUS) == 1
    db_session.commit()
    edge = db_session.scalar(select(WordLink).where(WordLink.edge_type == "GOVERNS"))
    assert edge.detail == "auf+Akk"


def test_inflected_form_counts_as_the_lemma(db_session):
    # "freue" (inflected) must satisfy the gate for lemma "freuen".
    assert citation_is_valid(
        {
            "from_lemma": "freuen",
            "to_lemma": "Wochenende",
            "edge_type": "COLLOCATES_WITH",
            "citation": "Ich freue mich auf das Wochenende.",
        },
        _CORPUS,
        Lemmatizer(),
    )


def test_stem_matcher_accepts_inflections_and_rejects_prefix_sharers():
    # The interim matcher must count a real inflection but not a longer word that
    # only shares a prefix - that separation is the citation gate's precision.
    lem = Lemmatizer()
    assert lem.matches("kommt", "kommen")
    assert lem.matches("freue", "freuen")
    assert not lem.matches("Kommission", "kommen")
    assert not lem.matches("Kommentar", "kommen")
    assert not lem.matches("Wartezeit", "warten")


def test_citation_naming_only_a_prefix_sharer_is_dropped():
    # A real corpus span that names "Kommission" is no evidence for "kommen"; the
    # gate must reject it rather than accept the shared prefix as the lemma.
    corpus = "Die Kommission trifft den Chef am Montag."
    assert not citation_is_valid(
        {
            "from_lemma": "kommen",
            "to_lemma": "Chef",
            "edge_type": "COLLOCATES_WITH",
            "citation": "Die Kommission trifft den Chef am Montag.",
        },
        corpus,
        Lemmatizer(),
    )


def test_edge_to_untracked_word_is_dropped(db_session):
    _persist_pair(db_session)  # no word "Urlaub" tracked
    cand = {
        "from_lemma": "Chef",
        "to_lemma": "Urlaub",
        "edge_type": "COLLOCATES_WITH",
        "citation": "Der Chef ist nicht der Mitarbeiter.",
    }

    assert persist_relational_edges(db_session, [cand], _CORPUS) == 0


def test_check_constraint_makes_uncited_llm_edge_unwritable(db_session):
    """Defence in depth: even bypassing persist_relational_edges, the DB itself
    refuses an LLM edge with no citation."""
    persist_words(db_session, [_verb("kommen", "kommen"), _verb("gehen", "gehen")])
    db_session.commit()
    a, b = db_session.scalars(select(Word.id)).all()

    db_session.add(
        WordLink(
            from_word_id=a,
            to_word_id=b,
            edge_type="SYNONYM",
            source="llm",
            citation=None,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


# --- extraction call -------------------------------------------------------------


class _FakeProvider:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.calls: list[list[dict]] = []

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        return self._reply


async def test_extract_parses_fenced_json_and_fences_corpus_as_data():
    provider = _FakeProvider(
        '```json\n{"edges": [{"from_lemma": "Chef", "to_lemma": '
        '"Mitarbeiter", "edge_type": "ANTONYM", "citation": "x"}]}\n```'
    )
    # client=None forces the offline path: the prompt comes from the bundled
    # fallback, so this exercises the real template, not an inline string.
    store = PromptStore(client=None)

    edges = await extract_relational_edges(
        _CORPUS, ["Chef", "Mitarbeiter"], provider, store
    )

    assert edges[0]["edge_type"] == "ANTONYM"
    # corpus reached the model inside a delimited data block (guardrail 6), and
    # the target lemmas were rendered into the prompt.
    user_content = provider.calls[0][1]["content"]
    assert "<source_text>" in user_content
    assert _CORPUS in user_content
    assert "Chef, Mitarbeiter" in user_content


async def test_extract_returns_empty_on_malformed_reply():
    provider = _FakeProvider("sorry, I could not find any relations!")
    edges = await extract_relational_edges(
        _CORPUS, ["Chef"], provider, PromptStore(client=None)
    )
    assert edges == []
