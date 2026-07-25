# Feasibility: 002 - LLM provider layer

- **Issue**: #2 · **Phase**: 1 · **Date**: 2026-07-25 · **Author**: Claude (reviewed by Hanzala)

## Goal
One async interface for all agent LLM calls: Groq `openai/gpt-oss-120b` → `llama-3.3-70b-versatile` → NVIDIA NIM fallback chain, streaming, retry/backoff.

## Approach options
1. **Thin wrapper over the OpenAI-compatible endpoints of both providers (chosen)** - Groq and NIM both speak the OpenAI schema, so one client class + base-URL/model config covers everything; ~150 LOC, fully ours.
2. LiteLLM proxy - battle-tested multi-provider routing, but another dependency/abstraction between us and errors; overkill for 2 providers.

## Risks & unknowns
- Groq free tier: ~1,000 req/day on large models, 6k TPM - a long session with per-utterance Corrector calls could brush TPM limits → batch corrector calls per 2–3 utterances if hit (documented mitigation, measure first).
- gpt-oss-120b German quality unverified by us → spike: 10 German A2-tutor exchanges, eyeball quality vs llama-3.3-70b before locking primary.

## Free-tier impact
Direct consumer of the main quota. Estimated: 1 session ≈ 20 tutor + 10 corrector calls ≈ 30 req → 3–5 sessions/day ≈ 150 req/day ≈ 15% of quota. Comfortable.

## Effort estimate
M (half-day) incl. mocked-HTTP tests for fallback paths.

## Verdict
**GO** - with the German-quality mini-spike inside the same branch.

## Mini-spike result (2026-07-25)
Ran 10 A2-tutor exchanges through both Groq models via the provider layer.
- `gpt-oss-120b`: good German, German-only, always ends with a follow-up question.
  Trends verbose and sometimes above A2 density (lists of cafes/sights). One
  transient empty reply, non-reproducible.
- `llama-3.3-70b`: slightly better A2 calibration - shorter, cleaner, one focused
  follow-up per turn.

Both are viable; not enough to overturn the locked primary. Kept `gpt-oss-120b`
primary as planned. Takeaway for #4: the CEFR-level output guardrail (guardrail 2)
earns its keep against gpt-oss verbosity - the prompt alone will not hold A2.
