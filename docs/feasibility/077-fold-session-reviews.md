# 077 - Fold FSRS reviews at session end

Wire #14's `apply_session_reviews` and #76's `classify_transcript` into the
session-close path so a real conversation advances FSRS state. The engine (write
side) and the classifier (transcript -> `[WordSignal]`) both exist and are merged;
nothing calls them from `end_session`, so `word_states` and `review_events` stay
empty no matter how many sessions run. This is the keystone that turns the two
merged pieces into a working loop.

## Where it wires in

`SessionWriter.end_session` (`app/agents/persistence.py`) already owns the
session-close transaction: it stamps `ended_at`, writes the caught errors, and folds
elapsed minutes into immersion, all inside one `_scope()` (commit-or-rollback). The
graph's `debrief` node (`app/agents/graph.py`) calls it through `asyncio.to_thread`
and holds the transcript (`state["messages"]`) and the session's CEFR level
(`state["level"]`). So the fold belongs *inside* `end_session`, driven by two new
args the debrief node passes: the same transaction and the same thread that already
run the close, no new seam.

`apply_session_reviews(db, signals, session_id)` adds rows to the session without
committing - it says so in its docstring and matches `record_session_immersion`. So
folding inside `_scope()` gives AC-1 (reviews land in the *same* transaction as the
close) for free.

## The load-bearing decision: where do `targets` come from?

`classify_transcript(messages, targets)` needs `[Target(word_id, lemma)]` - "the
words this session was meant to exercise." Nothing in the codebase produces that
list. Scenarios carry `redemittel` strings, not `word_id`s; curriculum-driven target
selection is #26, unbuilt. Three sourcing options were weighed (decision confirmed
with Hanzala 2026-09-02):

1. **(Chosen) Level deck, produced-only.** Targets = `Word` rows at the session's
   CEFR level (`state["level"]`). Classify, then fold **only** `UNPROMPTED` and
   `GLOSSED` signals - drop `AVOIDED`. First unprompted use creates a card (Good/Easy);
   a resurfaced due word advances; a glossed one grades Hard. This bootstraps the deck
   *from real speech*, which is the whole point of "grades come from conversation, not
   flashcard taps" (CLAUDE.md pedagogy), and directly fixes "`due_words` stays empty."
   Dropping `AVOIDED` is what makes the level deck a safe target set: without it, every
   level word the learner simply didn't say this session would grade `Again` and tank
   the stability of cards that were never relevant to the scenario. `AVOIDED` becomes
   meaningful only once curriculum assigns a small, scenario-scoped target set (#26);
   until then it has no honest referent and is left off.

2. **(Rejected) Due queue, all signals.** Targets = the current `due_words`, fold
   every signal. A pure resurfacing loop, but it never *bootstraps* - `word_states` is
   empty at the start, so the due queue is empty, so there are no targets, so nothing
   is ever graded and the deck stays empty forever. It also inherits the `AVOIDED`
   flood above. Fails the issue's own premise.

3. **(Rejected) Defer to a new state field curriculum fills.** Cleanest separation,
   but delivers a no-op today - the opposite of what a keystone issue is for.

The residual limitation of the chosen option, documented not hidden: a strong verb
the learner produces whose lemma the small spaCy model mangles (see #76's fronted-
separable note) is missed and simply not carded that session - a miss, never a wrong
grade. And because `AVOIDED` is off, a due card the learner had the chance to use but
dodged is not penalised yet; it just stays due. Both are conservative failure modes
(under-credit, never over-penalise), which is the right bias for a memory model.

Because the whole level deck is the target set, a homograph that shares a lemma at one
level (the noun `Morgen` vs the adverb `morgen`, the noun `Recht` vs the adverb
`recht`) would otherwise let one spoken token credit both cards. The classifier gates
each direct lemma hit on the token's coarse part of speech (spaCy UPOS folded to the
`Word.pos` taxonomy noun|verb|other), so producing the noun cards only the noun. A
mis-tagged production degrades to a miss, not a wrong grade - the same conservative
bias.

## Graceful degradation (guardrail 4, AC-3)

Classification calls spaCy (`get_nlp()` -> `spacy.load`), the one step that can
fail at runtime (model missing, load error). The fold runs inside a **SAVEPOINT**
(`db.begin_nested()`) wrapped in `try/except`: a failure rolls back only the partial
review writes and is logged, while the outer `_scope()` still commits the session
close (`ended_at`, errors, immersion). So a broken classifier degrades to "session
closed, no reviews this time" - it never blocks or hangs the close. The savepoint
(not a bare try/except) is what guarantees a mid-fold failure can't leave half-written
`word_states`/`review_events` behind to commit with the close.

Because that degrade is silent by design, a *persistent* failure (the pinned spaCy
model missing from a deployment) would otherwise look identical to the legitimate
"no words produced" no-op - every session closes fine, `due_words` just never fills.
So `/readyz` reports `classifier_ready` (a cheap `find_spec` on the model, no pipeline
load) and goes `degraded` when it is false, turning a broken install into a probe
signal instead of a log line no one watches.

## Shape

- `queries.py`: `words_at_level(db, level) -> list[Word]` - the read helper, pure,
  no dependency on the classifier (so no import cycle with `signals.py`).
- `signals.py`: `fold_session_reviews(db, messages, level, session_id, when=None)` -
  builds `Target`s from `words_at_level`, classifies, keeps `UNPROMPTED`/`GLOSSED`,
  calls `apply_session_reviews`. Lives here because `signals.py` already imports
  `reviews.py` (`WordSignal`); the reverse import would cycle.
- `persistence.py`: `end_session(..., messages=(), level=None)` calls
  `fold_session_reviews` inside `_scope()` under a savepoint + try/except. No
  transcript or no level -> skip (clean no-op).
- `graph.py`: `debrief` passes `messages=state["messages"]`, `level=state["level"]`.

Import cost: `signals.py` lazy-loads spaCy inside `get_nlp()` only, so importing it
at the top of `persistence.py` stays cheap; the model loads the first time a real
fold runs.

## Tests (written first)

- `words_at_level` returns only the level's words.
- `fold_session_reviews`: a transcript producing two of three level words creates
  exactly two cards + two events, the unspoken word gets nothing (no `AVOIDED` row),
  a glossed word grades Hard.
- `end_session` integration: stubbed transcript + level -> expected `word_states`/
  `review_events` rows, in the same commit as the close (AC-4).
- No-op: `end_session` with a transcript that produces no level word writes zero
  review rows and still closes cleanly (AC-2).
- Degradation: `fold_session_reviews` monkeypatched to raise -> session still closes
  (`ended_at` set, immersion written), zero review rows (AC-3).

## Effort

S-M. The classifier and engine are done; this is the wiring, the target-source
helper, and the degradation harness. No new deps, no schema change (`word_states`
and `review_events` already exist from #14).

## Verdict

**GO.** The pieces exist and the seam (`end_session` owning the close transaction)
is already the right place. The only real design call - the target set - is settled
in favour of bootstrapping the deck from real speech, which is what the pedagogy
rules already commit to.
