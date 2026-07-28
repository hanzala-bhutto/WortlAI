# Feasibility: 052 - STT accuracy on hesitant A2 German speech

- **Issue**: #52
- **Phase / Milestone**: Phase 1 - Voice Conversation Loop
- **Date**: 2026-07-28
- **Author**: Claude (reviewed by Hanzala)

## Goal
Groq Whisper mishears hesitant A2 German (false starts, long pauses,
self-corrections) even with mic-side noise handling from #8. A bad transcript
propagates into the Tutor's context for the rest of the turn and into the
debrief, so the learner gets replies keyed to something they didn't say.

## Approach options
1. **Swap `STT_MODEL` from `whisper-large-v3-turbo` to `whisper-large-v3`
   (chosen)** - per Groq's docs, `whisper-large-v3` has a 10.3% WER vs turbo's
   12%, still 189x real-time (turbo is 216x), and free-tier rate limits are
   identical for both models (20 req/min, 7200 audio-seconds/hour) - so this is
   a pure accuracy gain at the existing latency and cost budget. One-line
   change in `.env.example` and the default nobody overrides; no code touched.
2. **Bias the Whisper `prompt` param with the active scenario's `redemittel`
   (chosen)** - `GroqWhisper.transcribe` (`backend/app/voice/stt.py`) sends no
   `prompt`. Groq's transcription endpoint accepts one (max 224 tokens) that
   "guides model style" and helps spell unfamiliar words - exactly the chunks
   the learner is being drilled on (`Scenario.redemittel`,
   `backend/app/agents/scenarios.py:46`) and most likely to appear
   half-finished in a hesitant utterance. Threads the scenario's redemittel
   (already loaded in `voice/session.py` via `get_scenario`) into
   `transcribe(..., prompt=...)`. Doesn't fix disfluency itself but reduces
   misheard domain vocabulary, which is the more common failure Hanzala
   reported.
3. Client-side re-record affordance (redo an utterance before sending) -
   rejected for now. The mic is hold-to-talk (`MicButton.tsx`): botching an
   utterance today costs nothing, the learner just holds the button again.
   A review-before-send step adds a confirmation click to every single turn,
   which cuts against the fluency-first, forced-spoken-output design (CLAUDE.md
   pedagogy rules) for a benefit that's redundant with "just talk again."
   Revisit only if live use shows learners don't already retry naturally.
4. Silence trimming / VAD preprocessing before the Groq call - rejected for
   now. False starts and self-corrections are mid-utterance disfluencies, not
   leading/trailing dead air; Whisper already handles silence well per Groq's
   docs. No evidence this is the actual failure mode, and it adds a new
   audio-processing dependency for a guess. Revisit only with recorded
   evidence (e.g. from validating options 1-2 below) that specific utterances
   fail because of silence, not mis-heard words.

## Risks & unknowns
- No recorded corpus of Hanzala's actual hesitant-speech failures exists yet
  to benchmark against - the acceptance criteria asks for validation "against
  a small set of real hesitant-speech recordings." Plan: Hanzala records
  3-5 utterances that previously misheard live (or deliberately hesitant new
  ones) during manual testing after #52 lands; compare transcripts against
  today's turbo+no-prompt baseline. This is a live-validation step like #51's,
  not something unit tests can cover.
- `redemittel` prompt biasing is heuristic - Whisper's `prompt` param isn't a
  hard vocabulary constraint, so it may not measurably help. Low cost either
  way (one optional string, no new dependency), so worth shipping and
  measuring rather than pre-rejecting.
- Model swap only changes WER by ~1.7 points on average benchmarks: this is a
  small, one-line-of-config win, not a fix for hesitant speech specifically.
  Framed accurately in the PR description so it isn't oversold as "solves #52."

## Free-tier impact
None - `whisper-large-v3` shares the same free-tier rate limits as
`whisper-large-v3-turbo` (verified via Groq's docs), and both are well inside
the per-session audio-seconds cap already enforced in `stt.py`.

## Effort estimate
S (<2h): one `.env.example` default change, one new optional `prompt` param on
`GroqWhisper.transcribe` threaded from the scenario's redemittel in
`voice/session.py`, tests for the prompt being built and passed, plus the
manual recorded-audio validation pass.

## Verdict
**GO** on options 1 and 2. Options 3 and 4 deferred, not rejected outright -
tracked as follow-up only if live validation shows they're still needed after
1 and 2 ship.

## Live validation (2026-07-28)
First live pass (`baeckerei`, model still `-turbo` because the local `.env`
hadn't been updated to match `.env.example` yet) surfaced real mishears:
`"Falkornbrot"` (Vollkornbrot), `"ich brache vier Stoen"` (brauche vier
Stücke), `"Köten oder Kassenbahn"` (Karte oder Kassenbon). Checked against
`baeckerei`'s Redemittel (`Ich hätte gern …`, `Was kostet …?`, `Zwei
Brötchen, bitte.`, `Sonst noch etwas?`) - none of the mangled words are in
that fixed list. They're payment/quantity vocabulary (Vollkorn, Stücke, Karte,
Kassenbon) the Tutor introduced mid-conversation, exactly the "may not
measurably help" risk called out above.

Fix: `_redemittel_prompt()` in `voice/session.py` now also includes the
Tutor's own last reply (returned by `_drive_turn`, tracked as `last_reply` in
the session loop) alongside the static Redemittel, so the STT prompt for a
turn is biased toward whatever vocabulary the Tutor *just said* - the words a
learner is about to echo back. Re-test after this change and the `.env` model
swap to see whether these specific mishears clear up.
