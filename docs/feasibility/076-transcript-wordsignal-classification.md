# Feasibility: 076 - transcript -> WordSignal classification

- **Issue**: #76
- **Phase / Milestone**: Phase 2 (RAG + Learner Model + FSRS)
- **Date**: 2026-08-16
- **Author**: Claude (reviewed by Hanzala)

## Goal
Turn a finished session's transcript into the `[WordSignal]` list that #14's
`apply_session_reviews` already consumes - the classification step #14 deliberately
left out so the FSRS engine stayed testable without a live conversation. For each
target item the session was meant to exercise, emit `WordSignal(word_id, signal,
unprompted_uses)` where `signal` is `UNPROMPTED` (learner produced it with no prior
hint), `GLOSSED` (produced only after the Tutor said it first), `AVOIDED` (expected,
never produced), or `FAILED` (attempted wrong). This is the piece that makes a real
conversation move FSRS state, wired into the session-close path by #77.

## Approach options

1. **(Recommended) Lemmatize learner turns with `de_core_news_sm`, match against
   target `Word` lemmas, read gloss from turn order.** The transcript is already
   `messages: [{"role","content"}]` (SessionState). Lemmatize each `user` turn,
   build a per-turn set of lemmas (plus recombined separable-verb lemmas), and for
   every target:
   - never produced by the learner -> `AVOIDED`;
   - produced, and the same lemma appeared in an `assistant` turn *before* the
     learner's first producing turn -> `GLOSSED` (the Tutor handed it over);
   - produced with no prior Tutor mention -> `UNPROMPTED`, `unprompted_uses` = how
     many learner turns produced it (so `signal_to_rating`'s Good/Easy split fires).
   - `FAILED` comes from an optional injected `failed_lemmas` set (the Corrector's
     output, #12/#16); empty for now, so nothing hallucinates a FAILED. `FAILED` and
     `AVOIDED` both map to `Rating.Again` anyway, so this only affects the
     `review_events` audit column, never scheduling.

   Pure over its inputs (transcript + target list + optional failed set + an
   injectable `nlp`), so unit tests pin every signal over fixed transcripts with no
   live STT/LLM. spaCy is quarantined in one module with a lazily-loaded, cached
   pipeline, matching the "external deps behind one file" convention.

2. **(Rejected) Ask an LLM to judge which target words were used and how.** Costs a
   Groq call per session end, is non-deterministic, untestable offline, and puts an
   untrusted transcript in instruction position (guardrail 6). Lemmatization is
   deterministic, free, local, and is exactly what CLAUDE.md's pedagogy rules
   already specify ("spaCy `de_core_news_sm` lemmatizes transcripts").

## Risks & unknowns
Spike run against `de_core_news_sm==3.8.0` (`scratchpad/spacy_spike.py`):

- **Inflection collapse works.** `gehe` / `ging` / `gegangen` all lemmatize to
  `gehen`; `Kinder` -> `Kind`; `Hause` -> `Haus`. This is the core requirement and
  it holds.
- **Articles need no special handling.** `die` lemmatizes to `der` (POS `DET`), so it
  simply never matches a content-word target. Matching against the (small, typed)
  target lemma set is enough; POS-aware matching is a later refinement, not needed
  now.
- **Separable verbs, split form, work via the `svp` dependency.** `komme ... mit` is
  `komme(lemma=kommen)` with a child `mit(dep=svp, tag=PTKVZ)`; `rufe ... an` the
  same. The split prefix token (tag `PTKVZ`, dep `svp`) is tagged reliably in *every*
  position tested. Joined forms (`mitkommen`, participle `mitgebracht` -> `mitbringen`)
  already lemmatize whole, so a direct lemma hit covers them.
- **Fronted finite verbs break the head-verb lemma, so matching is target-driven.**
  This is the one place an early design (recombine prefix + `head.lemma_`) was wrong.
  The small model mis-lemmatizes a fronted finite verb: `Ruf mich an!` -> `Ruf`,
  `Wann rufst du an?` -> `rufst`, `Kommst du mit?` -> `Kommst` (tagged `NE`). So
  reconstructing `prefix + head.lemma` yields `anruf` / `anrufst` / garbage and the
  correct production **misses**. A miss here is *not* safe: it routes to `AVOIDED` ->
  `Rating.Again`, actively burying a card for a correct utterance, and symmetrically a
  Tutor gloss phrased as a question never enters `said_by_tutor`, so the echo
  over-credits as `UNPROMPTED`. **Fix:** split each separable *target* into
  `(prefix, base, stem)` up front and ask whether a turn carries an svp prefix token
  equal to that prefix whose head verb agrees with the base - by exact lemma when the
  model got it right, else by stem prefix (`kommst`.startswith(`komm`)), which
  survives the mis-lemmatization. Verified recovering `Ruf mich an!`, `Wann rufst du
  an?`, `Kommst du mit?`. The same-prefix constraint stops `an` + a `ruf-` verb from
  cross-matching a `mit-` target (`Ich fange an` stays `anrufen`-AVOIDED).
- **Residual, honestly bounded:** a fronted *strong* separable verb whose stem vowel
  changes (`nimmst mit` for `mitnehmen`, `fährt ab` for `abfahren`) still fails the
  stem match and degrades to `AVOIDED`. That tail is documented, not papered over as
  "safe"; `de_core_news_md` would shrink it if it ever bites, not worth the size now.
- **Redemittel phrases are out of scope here.** A `WordSignal` needs a `word_id`, and
  multi-word Redemittel ("Ich hätte gern...") are not first-class rows yet (CLAUDE.md
  defers a chunk state table until chunks become real rows). This issue classifies
  `Word`-row lemmas; scheduling Redemittel chunks is future work once they have IDs.
- **Case matching.** spaCy noun lemmas stay capitalized (`Suppe`), verbs lowercase;
  matching casefolds both sides. Collisions across POS (adv `morgen` vs noun
  `Morgen`) are possible but negligible for the small A2 target set; POS-aware
  matching is the refinement if it ever bites.

## Free-tier impact
None. spaCy is local CPU inference, no API. One-time model load (~15 MB package)
cached process-wide; classification is a few milliseconds per session-end transcript.
No Groq / Whisper / NIM / Langfuse quota touched.

## Effort estimate
M (half-day). Main cost driver is the target-driven separable-verb matching (prefix
split + lemma/stem agreement) and the turn-order gloss logic, plus the transcript
fixtures that pin each signal - including the fronted question/imperative forms.

## Verdict
**GO** - the spike confirms lemmatization does what the pedagogy rules assume; the
one real model weakness (fronted finite verbs) is handled by matching from the target
side rather than the transcript side, with a small, documented strong-verb residual,
and the classifier is a pure, offline-testable function over data the session already
has.
