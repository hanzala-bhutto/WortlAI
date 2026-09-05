"""Turn a session transcript into the [WordSignal] list the FSRS write side consumes.

This is the classification step #14 deliberately left out of reviews.py so the
scheduler stayed testable without a live conversation (see that module's docstring).
Given the transcript a session produced and the items it was meant to exercise, it
lemmatizes the *learner's* turns and decides, per target, how the chunk showed up:

  UNPROMPTED  produced with no prior Tutor mention   (grades Good, Easy if repeated)
  GLOSSED     produced only after the Tutor said it   (grades Hard)
  AVOIDED     expected but never produced             (grades Again)
  FAILED      externally flagged as attempted-wrong   (grades Again)

FAILED is not read from the transcript - a bare transcript cannot tell "said it
wrong" from "said it right", and FAILED and AVOIDED both map to Again anyway, so it
would change only the review_events audit column. It arrives as an optional set of
lemmas from the Corrector (#12/#16); empty for now. The gloss judgement is read from
turn order: if the target lemma appears in a Tutor turn *before* the learner's first
producing turn, the learner leaned on the hint.

Matching a separable verb (`anrufen`, `mitkommen`) is the one hard part. spaCy tags
the split prefix reliably in every position (`an`/`mit`/`auf` -> tag PTKVZ, dep svp,
pointing at its verb), but the small model mis-lemmatizes a *fronted* finite verb -
`Ruf ... an!`, `Kommst du mit?`, `Wann rufst du an?` lemmatize the verb to `Ruf` /
`Kommst` / `rufst`, so reconstructing prefix + head-lemma yields garbage. So matching
runs the other way: each separable target is split into (prefix, base) up front, and
a turn matches it when it carries an svp prefix token equal to that prefix whose head
verb agrees with the base - by lemma when the model got it right, else by stem prefix
(`kommst`.startswith(`komm`)), which survives the mis-lemmatization. The same-prefix
constraint keeps this from cross-matching a different `an-`/`mit-` verb. The residual
miss is a fronted *strong* separable verb whose stem vowel changes (`nimmst mit` for
`mitnehmen`); that degrades to AVOIDED, documented, not silently claimed safe.

spaCy is quarantined here (the "external deps behind one file" convention): the German
pipeline is loaded once, lazily, and cached process-wide. Everything else is a pure
function over (transcript, targets, failed_lemmas), so tests pin every signal over
fixed transcripts.

docs/feasibility/076-transcript-wordsignal-classification.md has the spike that
grounds the separable-verb and inflection handling below.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib.util import find_spec
from typing import TYPE_CHECKING, NamedTuple

from app.learner.grading import Signal
from app.learner.queries import words_at_level
from app.learner.reviews import WordSignal, apply_session_reviews

if TYPE_CHECKING:
    from datetime import datetime

    from spacy.language import Language
    from spacy.tokens import Doc
    from sqlalchemy.orm import Session as DbSession

# The signals a session-close fold acts on: production the learner actually managed.
# UNPROMPTED (Good/Easy) and GLOSSED (Hard) create or advance a card; AVOIDED is left
# off deliberately - see fold_session_reviews.
_GRADED_SIGNALS = frozenset({Signal.UNPROMPTED, Signal.GLOSSED})

# The German model the pedagogy rules pin (CLAUDE.md). Pinned in requirements.txt by
# wheel URL so it is importable without a separate `spacy download`.
_MODEL = "de_core_news_sm"

# German separable (and ambiguous separable/inseparable) verb prefixes, longest first
# so `zurückkommen` splits on `zurück`, not `zu`. Including the ambiguous ones
# (um/über/unter/durch/wieder) is safe: an inseparable verb never produces an svp
# prefix token, so the separable match path simply never fires for it.
_SEPARABLE_PREFIXES: tuple[str, ...] = tuple(
    sorted(
        {
            "auseinander",
            "entgegen",
            "gegenüber",
            "herunter",
            "herüber",
            "zusammen",
            "zurecht",
            "voraus",
            "vorbei",
            "voran",
            "zurück",
            "entlang",
            "herein",
            "heraus",
            "herauf",
            "hinein",
            "hinaus",
            "hinauf",
            "hinunter",
            "empor",
            "weiter",
            "durch",
            "wieder",
            "wider",
            "statt",
            "nach",
            "fest",
            "fort",
            "vor",
            "auf",
            "aus",
            "ein",
            "bei",
            "her",
            "hin",
            "los",
            "mit",
            "teil",
            "weg",
            "zu",
            "an",
            "ab",
            "um",
            "über",
            "unter",
            "dar",
        },
        key=len,
        reverse=True,
    )
)


class Target(NamedTuple):
    """One item the session was meant to exercise: the FSRS word_id, the lemma to match
    against the learner's speech, and its coarse part of speech. Kept decoupled from the
    ORM so the classifier is pure and the caller (fold_session_reviews) builds these
    from the level deck.

    `pos` (the Word.pos taxonomy: noun|verb|other) disambiguates homographs that share a
    lemma at one level - the noun `Morgen` from the adverb `morgen`, the noun `Recht`
    from the adverb `recht` - so producing one does not credit the other's card. Left
    None (the default) it matches pos-agnostically, the pre-#77 behaviour the grade-map
    tests pin."""

    word_id: int
    lemma: str
    pos: str | None = None


class _Separable(NamedTuple):
    """A separable-verb target pre-split for matching against split finite forms."""

    prefix: str  # the svp prefix token to look for, e.g. "an"
    base: str  # the base verb infinitive, e.g. "rufen"
    stem: str  # base with its infinitive ending stripped, e.g. "ruf"


class _Turn(NamedTuple):
    """Everything one transcript turn produced that a target can match against."""

    lemmas: frozenset[str]  # content lemmas, casefolded (catches joined/participle)
    lemma_pos: frozenset[tuple[str, str]]  # (lemma, coarse pos) for pos-aware matching
    svp: tuple[tuple[str, str, str], ...]  # (prefix, head_lemma, head_surface) per svp


def _coarse_pos(upos: str) -> str:
    """Map a spaCy UPOS tag onto the Word.pos taxonomy (noun|verb|other) a target
    carries, so a stored pos can gate a lemma match. AUX counts as verb (haben/sein/
    werden), PROPN as noun; adjectives, adverbs and the rest land in other."""
    if upos in ("NOUN", "PROPN"):
        return "noun"
    if upos in ("VERB", "AUX"):
        return "verb"
    return "other"


def _split_separable(lemma: str) -> _Separable | None:
    """Split a casefolded verb lemma into (prefix, base, stem) if it is a separable
    verb, else None. Requires a base of at least three characters that still looks
    like an infinitive, so `anrufen` -> (an, rufen, ruf) but `an` alone or a noun
    that merely starts with a prefix does not split."""
    for prefix in _SEPARABLE_PREFIXES:
        if not lemma.startswith(prefix):
            continue
        base = lemma[len(prefix) :]
        if len(base) >= 3 and base.endswith(("en", "n")):
            stem = base[:-2] if base.endswith("en") else base[:-1]
            return _Separable(prefix, base, stem)
    return None


def classifier_ready() -> bool:
    """Whether the pinned German model is importable - the readiness signal behind the
    session-close fold. When it is missing every fold degrades to "closed without
    reviews" (guardrail 4) and due_words silently stays empty, so /readyz reports this
    to make a broken install visible instead of leaving it to a per-session log line.
    Checks importability, not a full pipeline load, so the probe stays cheap."""
    return find_spec(_MODEL) is not None


@lru_cache(maxsize=1)
def get_nlp() -> Language:
    """The German pipeline, loaded once and cached. NER is disabled - we need the
    tagger, morphologizer, parser and lemmatizer (the parser attaches a separable
    prefix to its verb), but never entity spans, so paying for them is waste."""
    import spacy

    return spacy.load(_MODEL, disable=["ner"])


def _analyze(doc: Doc) -> _Turn:
    """Reduce a parsed turn to what matching needs: the casefolded content lemmas, and
    every separable-prefix token paired with its head verb's lemma and surface form.
    The svp linkage is trusted without a POS check on the head - it is reliable even
    where the head verb itself is mis-tagged (`Kommst du mit?`), and matching guards
    against a stray prefix by requiring the head to agree with a target's base verb."""
    content = [tok for tok in doc if not (tok.is_punct or tok.is_space)]
    lemmas = {tok.lemma_.casefold() for tok in content}
    lemma_pos = {(tok.lemma_.casefold(), _coarse_pos(tok.pos_)) for tok in content}
    svp = [
        (tok.text.casefold(), tok.head.lemma_.casefold(), tok.head.text.casefold())
        for tok in doc
        if tok.tag_ == "PTKVZ" or tok.dep_ == "svp"
    ]
    return _Turn(frozenset(lemmas), frozenset(lemma_pos), tuple(svp))


def _produces(
    turn: _Turn, lemma: str, sep: _Separable | None, pos: str | None = None
) -> bool:
    """Did this turn produce the target? A direct lemma hit covers ordinary words and
    the joined/participle forms of separable verbs (`mitkommen`, `mitgebracht`). A
    split separable verb is matched off its prefix token whose head verb agrees with
    the base - by exact lemma, or by stem prefix when the model mis-lemmatized a
    fronted finite verb. The prefix equality is what keeps `an` + a `ruf-` verb from
    matching a `mit-` target.

    When `pos` is given the direct hit must also agree with the token's coarse pos, so
    a homograph of a different pos (`morgen` the adverb vs `Morgen` the noun) does not
    match; None keeps the pos-agnostic hit. The svp path is verb-only, so a non-verb
    target never reaches it."""
    if pos is None:
        if lemma in turn.lemmas:
            return True
    elif (lemma, pos) in turn.lemma_pos:
        return True
    if sep is None or pos not in (None, "verb"):
        return False
    for prefix, head_lemma, head_surface in turn.svp:
        if prefix != sep.prefix:
            continue
        if head_lemma == sep.base:
            return True
        if len(sep.stem) >= 3 and head_surface.startswith(sep.stem):
            return True
    return False


def classify_transcript(
    messages: Sequence[Mapping[str, object]],
    targets: Sequence[Target],
    *,
    failed_lemmas: set[str] | None = None,
    nlp: Language | None = None,
) -> list[WordSignal]:
    """Classify each target against the transcript and return its [WordSignal].

    `messages` is the session's turn list, `{"role": "user"|"assistant", "content"}`,
    exactly as the graph state carries it; a turn whose content is not a string is
    treated as empty rather than crashing the whole fold. Only `user` turns are scored
    - we grade the learner's production, not the Tutor's speech. `failed_lemmas`
    (casefolded on the way in) forces those items to FAILED regardless of the
    transcript. `nlp` is injectable so a caller can reuse one pipeline; it defaults to
    the cached one.

    `unprompted_uses` counts producing *turns*, not occurrences: two uses in one breath
    is one retrieval, so it takes two separate turns to earn Easy. One WordSignal per
    target, in the given order, so a target duplicated across word_ids (a lemma that
    recurs across levels, or the same lemma under two parts of speech) each gets its
    own row, matched by its own (lemma, pos) key."""
    if not targets:
        return []
    nlp = nlp or get_nlp()
    failed = {lemma.casefold() for lemma in (failed_lemmas or set())}
    # One descriptor per distinct (lemma, pos) target: the pos disambiguates homographs
    # that share a lemma, and the separable split is a pure function of the lemma.
    # Duplicate word_ids with the same (lemma, pos) share a descriptor and a hit.
    Key = tuple[str, str | None]
    descriptors: dict[Key, _Separable | None] = {}
    for target in targets:
        key: Key = (target.lemma.casefold(), target.pos)
        if key not in descriptors:
            descriptors[key] = _split_separable(key[0])

    said_by_tutor: set[Key] = set()  # target keys the Tutor has produced so far
    glossed: set[Key] = set()  # produced by learner only after a prior Tutor mention
    produced_turns: Counter[Key] = Counter()  # learner turns that produced each key

    for message in messages:
        content = message.get("content")
        turn = _analyze(nlp(content if isinstance(content, str) else ""))
        hits = {k for k, sep in descriptors.items() if _produces(turn, k[0], sep, k[1])}
        role = message.get("role")
        if role == "assistant":
            said_by_tutor |= hits
        elif role == "user":
            for k in hits:
                # A gloss only counts if it preceded the learner's *first* use, so this
                # fires once, before produced_turns records the key.
                if produced_turns[k] == 0 and k in said_by_tutor:
                    glossed.add(k)
                produced_turns[k] += 1

    signals: list[WordSignal] = []
    for target in targets:
        key = (target.lemma.casefold(), target.pos)
        uses = produced_turns.get(key, 0)
        if key[0] in failed:
            signal = Signal.FAILED
        elif uses == 0:
            signal = Signal.AVOIDED
        elif key in glossed:
            signal = Signal.GLOSSED
        else:
            signal = Signal.UNPROMPTED
        signals.append(WordSignal(target.word_id, signal, max(uses, 1)))
    return signals


def fold_session_reviews(
    db: DbSession,
    messages: Sequence[Mapping[str, object]],
    level: str,
    session_id: int | None = None,
    when: datetime | None = None,
) -> None:
    """Grade a finished session's transcript into FSRS state (#77). The target deck
    is every Word at the session's CEFR level; the classifier decides how each showed
    up, and only what the learner actually produced is folded - UNPROMPTED and GLOSSED.

    AVOIDED is dropped, not scheduled: with the whole level deck as targets, an
    unspoken word means only "this scenario didn't call for it," not a failed review,
    so grading it Again would tank cards that were never in play. AVOIDED gets an
    honest referent once the curriculum (#26) assigns a small per-session target set;
    until then the fold only ever creates or advances a card off real production, which
    is exactly the "grades come from conversation" rule.

    The caller owns the outer transaction; this adds rows without committing. The
    CPU-bound classification (spaCy, no writes) runs first, outside any savepoint;
    only the writes go inside a savepoint, so a failure mid-batch rolls back the
    whole batch instead of leaving a half-written fold to commit with the close."""
    targets = [
        Target(word.id, word.lemma, word.pos) for word in words_at_level(db, level)
    ]
    signals = classify_transcript(messages, targets)
    produced = [s for s in signals if s.signal in _GRADED_SIGNALS]
    if not produced:
        return  # nothing graded -> no savepoint, no rows (the clean no-op close)
    with db.begin_nested():
        apply_session_reviews(db, produced, session_id, when)
