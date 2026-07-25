# Feasibility: 005 - Corrector agent v1

- **Issue**: #5 · **Phase**: 1 · **Date**: 2026-07-25 · **Author**: Claude (reviewed by Hanzala)

## Goal
Async per-utterance error analysis with Pydantic-validated output, staged severity policy (communication-breaking only at first), end-of-session debrief.

## Approach options
1. **Fire-and-forget task per utterance writing to session state (chosen)** - zero conversation latency; results collected at debrief.
2. Inline correction in the tutor call - one less call but couples jobs, adds latency, violates the no-interruption pedagogy. Rejected.

## Risks & unknowns
- LLM error-detection quality on A2 German (false positives are the real danger - demoralizing) → severity threshold conservative; few-shot German error examples in prompt; gold-dataset eval lands Phase 3.
- Structured-output reliability → JSON mode + Pydantic validation, one retry, then drop-and-log (never crash a session over a bad correction).

## Free-tier impact
+1 LLM call per utterance (~20/session). Combined with tutor ≈ 40 req/session - fine; batch 2–3 utterances per call if TPM pinches.

## Effort estimate
M (half-day).

## Verdict
**GO**.
