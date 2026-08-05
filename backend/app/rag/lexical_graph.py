"""The SQLite lexical graph: word nodes, typed edges, and their two provenances.

Two stores, one rule (docs/PLAN.md, docs/feasibility/012-lexical-graph.md):
structural facts come *deterministically* from #9's glossary parse and relational
facts come from an LLM *with a citation gate*. This module is the only writer of
`word_links`, so the anti-hallucination guarantee (guardrail 5) lives in one
place:

  - `persist_words` folds #9 `WordRecord`s into the `words` node table.
  - `derive_family_edges` builds IN_FAMILY edges by morphology alone - no model,
    no citation, because the derivation rule *is* the provenance.
  - `extract_relational_edges` + `persist_relational_edges` add COLLOCATES_WITH /
    SYNONYM / ANTONYM / GOVERNS edges, but only for candidates whose citation is
    found verbatim in the corpus we passed the model AND names both endpoints.
    Everything else is dropped, never stored.

The citation check needs to recognise an inflected form as its lemma
("freut" -> "freuen"). spaCy `de_core_news_sm` (issue #15) is the eventual
lemmatizer; until it lands, a conservative stem matcher stands in behind the same
`Lemmatizer` seam, so #15 is a one-argument swap, not a rewrite.
"""

import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.learner.models import Word, WordLink
from app.llmops.prompts import PromptStore
from app.rag.glossary_parser import WordRecord

# The versioned chat prompt (Langfuse, with a bundled fallback) carrying the
# extraction instructions and the delimited data block the untrusted corpus is
# rendered into. Never inline the prompt here - fetch it from the store, so it is
# editable in Langfuse without a redeploy (CLAUDE.md, #7).
EXTRACTION_PROMPT_NAME = "lexical-edge-extraction"

# The relational edge types the LLM track may produce. IN_FAMILY is deliberately
# absent: it is deterministic-only, so a model can never mint a family edge.
RelationalEdge = str  # COLLOCATES_WITH | GOVERNS | SYNONYM | ANTONYM
_RELATIONAL_TYPES = frozenset({"COLLOCATES_WITH", "GOVERNS", "SYNONYM", "ANTONYM"})
# Symmetric edge types get their reverse written too, so a walk finds them from
# either end and /graph-check's symmetry audit (check 6) holds by construction.
_SYMMETRIC_TYPES = frozenset({"IN_FAMILY", "SYNONYM", "ANTONYM"})

# A GOVERNS edge's detail is "preposition+Case". We store the case abbreviated
# (Akk/Dat/Gen/Nom, what /graph-check check 5 expects), but the model often emits
# the full German name ("mit+Dativ") or an English one, so accept those spellings
# and canonicalise rather than drop an otherwise valid government edge.
_GOVERNS_DETAIL_RE = re.compile(r"^([\wäöüß]+)\+(\w+)$")
_CASE_ALIASES = {
    "akk": "Akk",
    "akkusativ": "Akk",
    "accusative": "Akk",
    "dat": "Dat",
    "dativ": "Dat",
    "dative": "Dat",
    "gen": "Gen",
    "genitiv": "Gen",
    "genitive": "Gen",
    "nom": "Nom",
    "nominativ": "Nom",
    "nominative": "Nom",
}


def _normalize_govern_detail(detail: str | None) -> str | None:
    """Canonicalise a GOVERNS `detail` to "prep+Abbrev" (e.g. "mit+Dativ" ->
    "mit+Dat"), or None if it is not a recognisable preposition+case marker."""
    if not detail:
        return None
    match = _GOVERNS_DETAIL_RE.match(detail.strip())
    if match is None:
        return None
    prep, case = match.group(1), match.group(2).casefold()
    canonical = _CASE_ALIASES.get(case)
    return f"{prep}+{canonical}" if canonical else None


# --- word nodes -----------------------------------------------------------------


def persist_words(
    db: DbSession, records: Iterable[WordRecord], source: str = "glossary"
) -> list[Word]:
    """Upsert `WordRecord`s into the `words` table, keyed on (lemma, pos, level).

    `source` stamps provenance (`glossary`|`goethe`|`vision`) on every row written;
    it defaults to `"glossary"` so #9's callers are unchanged, and the Goethe path
    (#13) passes `"goethe"`. Re-ingesting the same source updates rows in place rather
    than duplicating them - the natural key the migration's UNIQUE constraint enforces.
    Returns the persisted `Word` rows (existing or new) in input order, so a caller can
    wire edges off them without a second query."""
    out: list[Word] = []
    for rec in records:
        word = db.scalar(
            select(Word).where(
                Word.lemma == rec.lemma,
                Word.pos == rec.pos,
                Word.level == rec.level,
            )
        )
        if word is None:
            word = Word(lemma=rec.lemma, pos=rec.pos, level=rec.level)
            db.add(word)
        word.lemma_raw = rec.lemma_raw
        word.article = rec.article
        word.plural = rec.plural
        word.verb_prefix = rec.verb.prefix if rec.verb else None
        word.verb_infinitive = rec.verb.infinitive if rec.verb else None
        word.verb_aux = rec.verb.perfect_auxiliary if rec.verb else None
        word.translation_en = rec.translation_en
        word.example_de = rec.example_de
        word.chapter = rec.chapter
        word.chapter_title = rec.chapter_title
        word.topic = rec.topic
        word.source = source
        word.source_page = rec.source_page
        word.needs_review = rec.needs_review
        out.append(word)
    db.flush()
    return out


# --- deterministic edges: IN_FAMILY ---------------------------------------------


def derive_family_edges(db: DbSession) -> int:
    """Build IN_FAMILY edges from verb morphology alone. Returns edges inserted.

    A verb's `verb_infinitive` is its family key: the base `sprechen` and every
    separable form (`mit|sprechen`, `aus|sprechen`) share it, so grouping by that
    key and connecting the members recovers the family without a model. Members
    are connected as a clique (every pair, both directions) rather than a star, so
    a family is correct even when its bare base verb never appears in the corpus -
    at this scale (families of a handful) the extra edges are free, and a walk
    from any member reaches the whole family in one hop.

    Idempotent: an edge already present (the UNIQUE natural key) is skipped, so
    re-running after a re-ingest adds only genuinely new family links."""
    families: dict[str, list[int]] = defaultdict(list)
    rows = db.execute(
        select(Word.id, Word.verb_infinitive).where(
            Word.pos == "verb", Word.verb_infinitive.is_not(None)
        )
    ).all()
    for word_id, infinitive in rows:
        families[infinitive].append(word_id)

    inserted = 0
    for members in families.values():
        if len(members) < 2:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                inserted += _add_edge(db, a, b, "IN_FAMILY", source="deterministic")
    db.flush()
    return inserted


# --- LLM edges: extraction + citation gate --------------------------------------


# The stem fallback only recognises SUFFIXAL inflection (a shared prefix), and the
# inflectional endings it can catch are short (-e, -st, -t, -en, -et, -end). Bounding
# how far the surface may run past the stem is what keeps a longer word that merely
# shares that prefix - a derivation or compound like "Kommission"/"kommen" or
# "Wartezeit"/"warten" - from passing as the lemma and validating a citation that is
# no evidence for the edge. It still cannot separate a derivation whose suffix is as
# short as an inflection ("Zeitung"/"Zeit"); that ambiguity waits for #15's real
# lemmatizer, so the LLM extraction path should run with spaCy injected before it is
# wired into the ingest pipeline. The gate biases to precision throughout: a missed
# inflection only drops an edge, a false match would admit an uncited relation.
_MAX_INFLECTION_SUFFIX = 3


class Lemmatizer:
    """Maps a German surface form to its lemma so the citation gate recognises
    "freut" as evidence for "freuen". The default is a deliberately conservative
    stem matcher (no dependency) tuned for precision, not recall; #15 injects spaCy
    `de_core_news_sm` for real lemmatization via the same `matches` seam."""

    def __init__(self, lemma_of: Callable[[str], str] | None = None) -> None:
        self._lemma_of = lemma_of

    def matches(self, surface: str, lemma: str) -> bool:
        """Does `surface` (a token from the corpus) realise `lemma`?"""
        surface, lemma = surface.casefold(), lemma.casefold().lstrip("|")
        if self._lemma_of is not None:
            return self._lemma_of(surface) == lemma
        if surface == lemma:
            return True
        # Stem fallback: the surface must extend the lemma's stem by no more than a
        # short inflectional ending. A long remainder means a different word that
        # merely shares a prefix ("Kommission" is not an inflection of "kommen"), and
        # a stem under four chars matches too much to trust.
        stem = lemma[:-2] if len(lemma) > 4 else lemma
        if len(stem) < 4 or not surface.startswith(stem):
            return False
        return len(surface) - len(stem) <= _MAX_INFLECTION_SUFFIX


def _parse_edges(reply: str) -> list[dict]:
    """Pull the edge list out of a model reply, tolerant of a ```json fence.
    Malformed JSON yields no edges rather than raising - a bad extraction drops
    its whole batch, it never reaches the DB (guardrail 1)."""
    text = reply.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    edges = data.get("edges") if isinstance(data, dict) else data
    return edges if isinstance(edges, list) else []


async def extract_relational_edges(
    corpus: str,
    lemmas: list[str],
    provider,  # app.llm.provider.LLMProvider, duck-typed for test fakes
    prompt_store: PromptStore,
    *,
    label: str = "production",
) -> list[dict]:
    """Ask the LLM for relational edges evidenced by `corpus`. Returns raw
    candidate dicts - unvalidated. `persist_relational_edges` is where the
    citation gate decides which survive; keeping extraction and validation
    separate means a hallucinated citation is caught by our check, not trusted
    because the model was asked nicely.

    The prompt is fetched from `prompt_store` (Langfuse, bundled fallback), never
    inlined here. The untrusted corpus is rendered into its delimited data block
    (guardrail 6): the model reads it, never obeys it.

    `corpus` is passed to the model as given; the ingest caller is responsible for
    chunking it to a bounded size and capping calls per run (guardrail 3), so this
    building block never truncates a span mid-citation."""
    if not corpus.strip() or not lemmas:
        return []
    prompt = prompt_store.get_chat(
        EXTRACTION_PROMPT_NAME,
        label=label,
        variables={"lemmas": ", ".join(lemmas), "corpus": corpus},
    )
    reply = await provider.complete(prompt.messages, temperature=0.0)
    return _parse_edges(reply)


def citation_is_valid(
    candidate: dict,
    corpus: str,
    lemmatizer: Lemmatizer,
) -> bool:
    """The anti-hallucination gate. A candidate survives only if:

    1. it names two distinct target endpoints and an allowed edge_type;
    2. its citation is a non-empty substring found verbatim in `corpus` (the model
       quoted real text, not an invented sentence);
    3. that citation actually contains both endpoint lemmas (inflections count via
       the lemmatizer) - a real span that happens to mention neither word is no
       evidence for the edge;
    4. a GOVERNS edge additionally carries a well-formed prep+Case `detail`."""
    edge_type = candidate.get("edge_type")
    from_lemma = (candidate.get("from_lemma") or "").strip()
    to_lemma = (candidate.get("to_lemma") or "").strip()
    citation = (candidate.get("citation") or "").strip()
    if edge_type not in _RELATIONAL_TYPES:
        return False
    if not from_lemma or not to_lemma or from_lemma == to_lemma:
        return False
    if not citation or citation not in corpus:
        return False
    tokens = re.findall(r"[\wäöüß|]+", citation)
    if not any(lemmatizer.matches(t, from_lemma) for t in tokens):
        return False
    if not any(lemmatizer.matches(t, to_lemma) for t in tokens):
        return False
    # A GOVERNS edge additionally needs a recognisable prep+case detail.
    return edge_type != "GOVERNS" or (
        _normalize_govern_detail(candidate.get("detail")) is not None
    )


def persist_relational_edges(
    db: DbSession,
    candidates: Iterable[dict],
    corpus: str,
    *,
    lemmatizer: Lemmatizer | None = None,
) -> int:
    """Validate LLM candidates against `corpus` and write the survivors. Returns
    the number of edges inserted. Endpoints must resolve to existing `words`
    rows - an edge to a word we do not track is dropped, since the graph has
    nowhere to hang it. Symmetric types (SYNONYM/ANTONYM) get their reverse edge
    too."""
    lemmatizer = lemmatizer or Lemmatizer()
    inserted = 0
    for cand in candidates:
        if not citation_is_valid(cand, corpus, lemmatizer):
            continue
        from_word = _lookup_word(db, cand["from_lemma"].strip())
        to_word = _lookup_word(db, cand["to_lemma"].strip())
        if from_word is None or to_word is None:
            continue
        # GOVERNS stores the canonical "prep+Abbrev"; other types carry no detail.
        detail = (
            _normalize_govern_detail(cand.get("detail"))
            if cand["edge_type"] == "GOVERNS"
            else None
        )
        inserted += _add_edge(
            db,
            from_word.id,
            to_word.id,
            cand["edge_type"],
            source="llm",
            citation=cand["citation"].strip(),
            detail=detail,
        )
    db.flush()
    return inserted


# --- reads: family walk ---------------------------------------------------------


def word_family(db: DbSession, lemma: str, *, max_hops: int = 2) -> list[Word]:
    """Every word reachable from `lemma` over IN_FAMILY edges, up to `max_hops`.

    The Curriculum agent's "he just learned *ziehen*, teach *umziehen* next" walk.
    Clique edges make one hop already return a full family; the second hop is
    insurance against a sparser edge set and stays milliseconds at this scale.
    Excludes the seed word itself - a family is what surrounds a word."""
    seeds = db.scalars(select(Word).where(Word.lemma == lemma)).all()
    seen = {w.id for w in seeds}
    frontier = set(seen)
    for _ in range(max_hops):
        if not frontier:
            break
        neighbours = db.scalars(
            select(WordLink.to_word_id).where(
                WordLink.from_word_id.in_(frontier),
                WordLink.edge_type == "IN_FAMILY",
            )
        ).all()
        frontier = {n for n in neighbours if n not in seen}
        seen |= frontier
    family_ids = seen - {w.id for w in seeds}
    if not family_ids:
        return []
    return list(db.scalars(select(Word).where(Word.id.in_(family_ids))).all())


# --- internals ------------------------------------------------------------------


def _lookup_word(db: DbSession, lemma: str) -> Word | None:
    """First word matching a lemma. Ambiguity across pos/level is rare in this
    corpus and not worth a disambiguation pass for edge endpoints; the family
    walk reads by lemma anyway."""
    return db.scalar(select(Word).where(Word.lemma == lemma))


def _edge_exists(db: DbSession, from_id: int, to_id: int, edge_type: str) -> bool:
    return (
        db.scalar(
            select(WordLink.id).where(
                WordLink.from_word_id == from_id,
                WordLink.to_word_id == to_id,
                WordLink.edge_type == edge_type,
            )
        )
        is not None
    )


def _add_edge(
    db: DbSession,
    from_id: int,
    to_id: int,
    edge_type: str,
    *,
    source: str,
    citation: str | None = None,
    detail: str | None = None,
) -> int:
    """Insert one edge (and its reverse for symmetric types), skipping any that
    already exist. Returns how many rows were added, so callers can count real
    inserts across a re-ingest."""
    if from_id == to_id:
        return 0
    pairs = [(from_id, to_id)]
    if edge_type in _SYMMETRIC_TYPES:
        pairs.append((to_id, from_id))
    added = 0
    for a, b in pairs:
        if _edge_exists(db, a, b, edge_type):
            continue
        db.add(
            WordLink(
                from_word_id=a,
                to_word_id=b,
                edge_type=edge_type,
                source=source,
                citation=citation,
                detail=detail,
            )
        )
        added += 1
    return added
