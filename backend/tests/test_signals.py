"""Classify a transcript into the [WordSignal] list apply_session_reviews consumes (#76).

Every case is a fixed transcript plus a target list, so the classifier is pinned
with no live STT/LLM. The signals it must tell apart:

  UNPROMPTED  learner produced the lemma with no prior Tutor mention
  GLOSSED     produced, but the Tutor said it first (handed it over)
  AVOIDED     expected, never produced
  FAILED      externally flagged as attempted-wrong (Corrector, empty for now)

The lemmatizer has to see through German inflection and separable verbs, so those
get their own cases rather than being assumed.
"""

from fsrs import Rating
from sqlalchemy import select

from app.learner.grading import Signal
from app.learner.models import ReviewEvent, Word, WordState
from app.learner.signals import Target, classify_transcript, fold_session_reviews

# One target keyed by the lemma the learner is meant to produce. word_id is opaque
# here - the classifier never touches the DB, it just threads it back out.
GEHEN = Target(word_id=1, lemma="gehen")
KOMMEN = Target(word_id=2, lemma="kommen")
SUPPE = Target(word_id=3, lemma="Suppe")
MITKOMMEN = Target(word_id=4, lemma="mitkommen")
ANRUFEN = Target(word_id=5, lemma="anrufen")


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _tutor(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _by_word(signals):
    return {s.word_id: s for s in signals}


def _deck_word(db, lemma: str, pos: str = "verb", level: str = "A2") -> Word:
    word = Word(lemma=lemma, lemma_raw=lemma, pos=pos, level=level, source="glossary")
    db.add(word)
    db.commit()
    return word


def test_unprompted_use_grades_from_learner_turn():
    """Learner produces the target with no prior Tutor mention -> UNPROMPTED once."""
    signals = classify_transcript([_user("Ich gehe jeden Tag zur Arbeit.")], [GEHEN])
    assert len(signals) == 1
    assert signals[0].word_id == GEHEN.word_id
    assert signals[0].signal is Signal.UNPROMPTED
    assert signals[0].unprompted_uses == 1


def test_inflected_forms_collapse_to_the_lemma():
    """gegangen is still gehen - the whole point of lemmatizing the transcript."""
    signals = classify_transcript([_user("Gestern bin ich gegangen.")], [GEHEN])
    assert signals[0].signal is Signal.UNPROMPTED


def test_article_does_not_block_a_noun_match():
    """`die Suppe` matches the target Suppe; the article lemmatizes away on its own."""
    signals = classify_transcript([_user("Ich hätte gern die Suppe.")], [SUPPE])
    assert signals[0].signal is Signal.UNPROMPTED


def test_separable_verb_split_across_the_clause_recombines():
    """`rufe ... an` is anrufen; matching the bare `rufe` (=rufen) would miss it."""
    signals = classify_transcript([_user("Ich rufe dich morgen an.")], [ANRUFEN])
    assert signals[0].signal is Signal.UNPROMPTED


def test_separable_verb_in_imperative_still_counts():
    """`Ruf ... an!` fronts the finite verb; the small model lemmatizes it to `Ruf`,
    so matching on the head lemma would miss a correct production and grade it Again.
    The svp prefix + stem match recovers it -> UNPROMPTED, not AVOIDED."""
    signals = classify_transcript([_user("Ruf mich morgen an!")], [ANRUFEN])
    assert signals[0].signal is Signal.UNPROMPTED


def test_separable_verb_in_a_question_still_counts():
    """`Wann rufst du an?` fronts `rufst` (mis-lemmatized), the exact case the old
    head-lemma recombination dropped. Stem match off the reliable svp prefix keeps
    the correct production from being scored AVOIDED."""
    signals = classify_transcript([_user("Wann rufst du an?")], [ANRUFEN])
    assert signals[0].signal is Signal.UNPROMPTED


def test_tutor_gloss_via_a_question_is_detected():
    """A Tutor offering the word as `Kommst du mit?` (fronted, mis-tagged as a proper
    noun) must still land in said_by_tutor, or the learner's echo scores UNPROMPTED
    and over-credits. The prefix + stem match sees mitkommen in the Tutor turn."""
    signals = classify_transcript(
        [_tutor("Kommst du morgen mit?"), _user("Ja, ich komme gern mit.")],
        [MITKOMMEN],
    )
    assert signals[0].signal is Signal.GLOSSED


def test_same_prefix_different_verb_does_not_false_match():
    """`Ich fange an` is anfangen, not anrufen; both split on `an`, so a bare prefix
    match would mis-credit anrufen. Requiring the head verb to agree with the base
    keeps anrufen AVOIDED."""
    signals = classify_transcript([_user("Ich fange gleich an.")], [ANRUFEN])
    assert signals[0].signal is Signal.AVOIDED


def test_repeated_unprompted_use_counts_turns_for_the_easy_split():
    """Two unprompted turns -> unprompted_uses == 2, which is what tips Good to Easy
    in signal_to_rating; a single lucky hit must not."""
    signals = classify_transcript(
        [
            _user("Ich gehe zur Arbeit."),
            _tutor("Schön."),
            _user("Danach gehe ich nach Hause."),
        ],
        [GEHEN],
    )
    assert signals[0].signal is Signal.UNPROMPTED
    assert signals[0].unprompted_uses == 2


def test_tutor_saying_it_first_makes_it_glossed():
    """The Tutor produced mitkommen before the learner echoed it -> GLOSSED, not
    UNPROMPTED: the learner leaned on the hint, so it grades Hard downstream."""
    signals = classify_transcript(
        [_tutor("Willst du heute Abend mitkommen?"), _user("Ja, ich komme gern mit.")],
        [MITKOMMEN],
    )
    assert signals[0].signal is Signal.GLOSSED


def test_learner_first_is_unprompted_even_if_tutor_repeats_later():
    """A gloss only counts if it came before the learner's first use; a Tutor echo
    afterwards must not retroactively downgrade an unprompted production."""
    signals = classify_transcript(
        [_user("Ich komme mit."), _tutor("Schön, dass du mitkommst.")],
        [MITKOMMEN],
    )
    assert signals[0].signal is Signal.UNPROMPTED


def test_expected_but_never_produced_is_avoided_not_dropped():
    """An untouched target still appears in the output as AVOIDED - the FSRS engine
    needs to see the miss, not have it silently missing."""
    signals = classify_transcript([_user("Ich gehe zur Arbeit.")], [GEHEN, KOMMEN])
    by_word = _by_word(signals)
    assert by_word[GEHEN.word_id].signal is Signal.UNPROMPTED
    assert by_word[KOMMEN.word_id].signal is Signal.AVOIDED


def test_failed_lemmas_override_to_failed():
    """A lemma the Corrector flagged as attempted-wrong is FAILED even though the
    learner produced it; this is the seam #12/#16 fills, empty for now."""
    signals = classify_transcript(
        [_user("Ich gehe zur Arbeit.")], [GEHEN], failed_lemmas={"gehen"}
    )
    assert signals[0].signal is Signal.FAILED


def test_no_targets_is_empty():
    """A session with nothing scheduled is a clean no-op, matching #77's no-op close."""
    assert classify_transcript([_user("Ich gehe zur Arbeit.")], []) == []


def test_only_learner_turns_are_scored():
    """A target the Tutor uses but the learner never does is AVOIDED: we grade the
    learner's production, not the Tutor's speech."""
    signals = classify_transcript(
        [_tutor("Ich gehe jetzt."), _user("Okay, tschüss.")], [GEHEN]
    )
    assert signals[0].signal is Signal.AVOIDED


def test_non_string_content_is_skipped_not_crashed():
    """A turn whose content is not a string (a None, or a structured content payload)
    must degrade to an empty turn, not abort the whole session fold with a TypeError -
    the classifier runs at session close where one bad turn should not lose the rest."""
    signals = classify_transcript(
        [
            {"role": "user", "content": None},
            {"role": "user", "content": ["structured", "parts"]},
            _user("Ich gehe zur Arbeit."),
        ],
        [GEHEN],
    )
    assert signals[0].signal is Signal.UNPROMPTED
    assert signals[0].unprompted_uses == 1


# --- fold_session_reviews: the #77 seam that grades the level deck from a transcript


def test_fold_schedules_only_the_words_the_learner_produced(db_session):
    """Two of three A2 words come up in speech -> two cards, two events; the untouched
    word gets nothing. AVOIDED is dropped, so an unspoken deck word is never graded."""
    gehen = _deck_word(db_session, "gehen")
    suppe = _deck_word(db_session, "Suppe", pos="noun")
    _deck_word(db_session, "kaufen")  # never said -> no card, no Again row

    fold_session_reviews(
        db_session,
        [_user("Ich gehe zur Arbeit und hätte gern die Suppe.")],
        level="A2",
    )
    db_session.commit()

    carded = {s.word_id for s in db_session.scalars(select(WordState))}
    assert carded == {gehen.id, suppe.id}


def test_fold_grades_a_glossed_word_hard_and_an_unprompted_one_good(db_session):
    """A word the Tutor handed over first grades Hard; one produced cold grades Good."""
    gehen = _deck_word(db_session, "gehen")
    suppe = _deck_word(db_session, "Suppe", pos="noun")

    fold_session_reviews(
        db_session,
        [
            _user("Ich gehe jeden Tag zur Arbeit."),
            _tutor("Möchten Sie die Suppe probieren?"),
            _user("Ja, ich nehme die Suppe."),
        ],
        level="A2",
    )
    db_session.commit()

    events = {e.word_id: e.rating for e in db_session.scalars(select(ReviewEvent))}
    assert events[gehen.id] == Rating.Good.value  # unprompted
    assert events[suppe.id] == Rating.Hard.value  # glossed (Tutor said it first)


def test_fold_with_no_production_writes_nothing(db_session):
    """A transcript that touches no deck word is a clean no-op - no rows, no error."""
    _deck_word(db_session, "gehen")

    fold_session_reviews(db_session, [_user("Okay, tschüss.")], level="A2")
    db_session.commit()

    assert db_session.scalars(select(WordState)).all() == []
    assert db_session.scalars(select(ReviewEvent)).all() == []


def test_fold_on_an_empty_deck_is_a_no_op(db_session):
    """No words at the level -> no targets -> nothing folded, matching #77's no-op."""
    fold_session_reviews(db_session, [_user("Ich gehe zur Arbeit.")], level="A2")
    db_session.commit()

    assert db_session.scalars(select(WordState)).all() == []


def test_fold_disambiguates_a_homograph_by_pos(db_session):
    """The noun `Morgen` (morning) and the adverb `morgen` (tomorrow) share a lemma at
    one level. Producing the noun must card the noun word only, not the adverb - the
    pos gate is what stops one surface form from crediting both cards (#77 review)."""
    morgen_noun = _deck_word(db_session, "Morgen", pos="noun")
    morgen_adv = _deck_word(db_session, "morgen", pos="other")

    fold_session_reviews(
        db_session, [_user("Guten Morgen, wie geht es Ihnen?")], level="A2"
    )
    db_session.commit()

    carded = {s.word_id for s in db_session.scalars(select(WordState))}
    assert carded == {morgen_noun.id}  # the adverb card is untouched
    assert morgen_adv.id not in carded


def test_classify_pos_gates_the_lemma_match():
    """At the classifier level: an adverb-pos target does not match the noun token, so
    a bare lemma set (pre-review behaviour) would have over-credited it."""
    noun = Target(word_id=1, lemma="Morgen", pos="noun")
    adverb = Target(word_id=2, lemma="morgen", pos="other")

    signals = _by_word(classify_transcript([_user("Guten Morgen!")], [noun, adverb]))

    assert signals[1].signal is Signal.UNPROMPTED  # noun produced
    assert signals[2].signal is Signal.AVOIDED  # adverb not produced
