# Feasibility: 014 - FSRS engine over chunks with conversation-derived grading

- **Issue**: #14
- **Phase / Milestone**: Phase 2 - RAG + Learner Model + FSRS
- **Date**: 2026-08-08
- **Author**: Claude (reviewed by Hanzala)

## Goal
Give every learnable item a spaced-repetition schedule that is driven by how
Hanzala actually speaks, not by flashcard taps. After a session ends, each item
he was expected to use gets an FSRS review whose grade comes from conversation
signal (used unprompted -> Good/Easy, needed a gloss -> Hard, failed or avoided
-> Again), its memory state advances, and a due-queue query can name what to
resurface next. User-visible outcome: the review queue and the debrief stop
being static lists and start reflecting real recall, which is the input every
later Phase 2/3 feature (curriculum, /progress, review UI) reads.

## What already exists (and what does not)
- `Word` (learner/models.py) is the only persisted learnable lexical row: a
  single lemma from the glossary (#9) or Goethe list (#13). Its docstring
  already reserves the hook: *"#14 (FSRS word_states) hang off the same table."*
- Redemittel - the chunks the pedagogy says cards should be - are **not**
  persisted anywhere. #10's vision pipeline yields `RedemittelSet`/
  `RedemittelPhrase` Pydantic models that are never stored, and scenario
  Redemittel are static tuples in `agents/scenarios.py`. There is no `chunk`
  table.
- py-fsrs is named in requirements.txt's header comment but is **not** a
  dependency line and is not installed.

So the item FSRS attaches to, today, can only be `Word`. That collides with the
"chunks, not words" rule and is the central design decision below.

## Approach options

### Item identity: what a card is attached to
1. **(Recommended) Card subject = `words.id` now, engine kept subject-agnostic.**
   The acceptance criteria are literally word-centric ("word states update after
   a session"), and `Word` is the only row that exists to schedule. Store the
   FSRS state in a new `word_states` table with a unique FK to `words.id`. Keep
   the scheduler wrapper and the grade mapper ignorant of *what* the item is -
   they take an opaque item id and a signal - so when Redemittel become
   first-class rows (a follow-up issue), a second `chunk_states` table or a
   widened FK reuses the same engine with no rewrite. This honours the rule
   *"single words only where needed"* for now without pretending chunks are
   persisted when they are not.
2. **Introduce a polymorphic `learnable_item` table in this issue** that both
   Words and Redemittel feed, and hang state off it. Rejected as scope: it
   forces Redemittel persistence (a vision-ingest change, its own issue) into
   #14 and inflates an M task into an L one, for a table we can add later
   without touching the engine.

### Where the FSRS library lives
- **(Recommended)** Quarantine py-fsrs behind one file, `learner/fsrs_engine.py`,
  the same discipline CLAUDE.md applies to `llm/provider.py` / `voice/tts.py`.
  Nothing else imports `fsrs`. The wrapper exposes `new_card()`,
  `review(card_json, rating, when) -> (card_json, due, state)` and swallows the
  library's `Scheduler`/`Card`/`ReviewLog` types. Swapping FSRS versions or the
  scheduler config is then a one-file change.

### State storage shape
- **(Recommended)** `word_states`: `word_id` (unique FK), `due` (indexed,
  tz-aware UTC), `state` (Learning/Review/Relearning string), `stability`,
  `difficulty`, `reps`, `lapses`, `last_reviewed_at`, and `card_json` (the
  lossless `Card.to_dict()` blob). Mirror `due`/`state` into real columns so the
  due-queue query is a plain indexed `WHERE due <= now ORDER BY due`; keep
  `card_json` as the round-trip source of truth so we never reconstruct FSRS
  internals by hand. Alternative (JSON-only, no mirrored columns) rejected: the
  due queue is the hot read and must be indexable in SQL.
- `review_events`: append-only log - `word_id`, `session_id` (nullable FK),
  `rating` (1-4), `signal` (unprompted|glossed|failed|avoided), `reviewed_at`.
  This is both the audit trail and the training data the FSRS optimizer needs;
  CLAUDE.md defers the optimizer until 2-3 months of it exist, so #14's job is
  only to start collecting it.

### Grade mapping (the unit-tested core)
- A pure function `signal_to_rating(signal, ...) -> Rating` encoding the
  pedagogy rule verbatim: `UNPROMPTED -> Good` (Easy only on a strong repeat
  signal), `GLOSSED -> Hard`, `FAILED -> Again`, `AVOIDED -> Again`. Thresholds
  are named constants, and the mapping table is the thing the acceptance
  criteria's "unit tests for grade mapping" pin. It takes an **already
  classified** signal enum, not raw text.

## Scope boundary
#14 delivers: the `fsrs_engine` wrapper, the two tables + Alembic migration, the
grade mapper, the due-queue query in `queries.py`, and an
`apply_session_reviews(db, [(word_id, signal), ...], session_id)` entry point
that writes state + events transactionally. #14 does **not** include extracting
the signals from a transcript (spaCy lemmatization of what was said, unprompted
-vs-gloss detection). That NLP is a separate concern the session-end path owns;
#14 takes the classified list as input so it stays testable without a
conversation. Flag this split for Hanzala - it is the main scope call.

## Risks & unknowns
- **Chunks-vs-words tension** -> keep the engine subject-agnostic and key on
  `word_id` now; a `chunk_states` table reuses it later. Documented above so the
  choice is deliberate, not accidental.
- **Card serialization round-trip** -> store `card_json` losslessly and unit-test
  `new -> review -> to_dict -> from_dict -> review` equality of the derived
  schedule. py-fsrs 6.3.1 documents `Card.to_dict()/from_dict()`; confirm exact
  method names on install and pin them in the wrapper.
- **Timezone** -> py-fsrs is UTC-only; our `_utcnow()` is already UTC-aware, so
  `due` stays tz-aware end to end. Test that a naive datetime never reaches the
  scheduler.
- **Grade boundary is a judgement call** (Good vs Easy) -> encode the CLAUDE.md
  rule literally, expose thresholds as constants, and let the grade-map test be
  the record of the decision.
- **Version pin** -> add `fsrs==6.3.1` (current, released 2026-03-10; pure
  Python, no native deps) as its own requirements.txt line with a comment tying
  it to #14.

## Free-tier impact
None. py-fsrs is pure-Python local computation - no Groq, Whisper, NIM, or
Langfuse calls. Scheduling and the due query are SQLite reads/writes. Zero
external quota consumed.

## Effort estimate
M (half-day). Cost driver is the state table + lossless serialization round-trip
and its migration; the grade mapper and due query are small and test-first.

## Verdict
**GO** - the engine is a thin, dependency-quarantined wrapper over a stable
pure-Python library, and keying cards on `words.id` while holding the engine
subject-agnostic satisfies the acceptance criteria today without foreclosing
chunk cards later.
