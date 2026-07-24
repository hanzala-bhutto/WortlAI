# Feasibility: 002 — LLM provider layer

- **Issue**: #2 · **Phase**: 1 · **Date**: 2026-07-25 · **Author**: Claude (reviewed by Hanzala)

## Goal
One async interface for all agent LLM calls: Groq `openai/gpt-oss-120b` → `llama-3.3-70b-versatile` → NVIDIA NIM fallback chain, streaming, retry/backoff.

## Approach options
1. **Thin wrapper over the OpenAI-compatible endpoints of both providers (chosen)** — Groq and NIM both speak the OpenAI schema, so one client class + base-URL/model config covers everything; ~150 LOC, fully ours.
2. LiteLLM proxy — battle-tested multi-provider routing, but another dependency/abstraction between us and errors; overkill for 2 providers.

## Risks & unknowns
- Groq free tier: ~1,000 req/day on large models, 6k TPM — a long session with per-utterance Corrector calls could brush TPM limits → batch corrector calls per 2–3 utterances if hit (documented mitigation, measure first).
- gpt-oss-120b German quality unverified by us → spike: 10 German A2-tutor exchanges, eyeball quality vs llama-3.3-70b before locking primary.

## Free-tier impact
Direct consumer of the main quota. Estimated: 1 session ≈ 20 tutor + 10 corrector calls ≈ 30 req → 3–5 sessions/day ≈ 150 req/day ≈ 15% of quota. Comfortable.

## Effort estimate
M (half-day) incl. mocked-HTTP tests for fallback paths.

## Verdict
**GO** — with the German-quality mini-spike inside the same branch.
