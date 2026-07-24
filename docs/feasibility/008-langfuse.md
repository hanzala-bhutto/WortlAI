# Feasibility: 008 — Langfuse tracing + prompt management

- **Issue**: #7 · **Phase**: 1 · **Date**: 2026-07-25 · **Author**: Claude (reviewed by Hanzala)

## Goal
Trace every agent/LLM call (session id, agent, model, latency, tokens); all prompts versioned in Langfuse Prompt Management, fetched at runtime with cache fallback — from Phase 1 so history exists from day one.

## Approach options
1. **Langfuse Python SDK decorators + prompt client with local disk cache (chosen)** — first-party, minimal code; cache makes the app work offline.
2. OpenTelemetry + self-hosted Langfuse — self-hosting adds Postgres/ClickHouse containers; cloud free tier (50k observations/mo) is plenty for one user. Defer self-hosting decision indefinitely.

## Risks & unknowns
- Free-tier observation cap: ~40 traced calls/session × 5 sessions/day × 30 days ≈ 6k/mo — 12% of cap. Comfortable.
- Cloud dependency for prompts → mandatory disk cache + bundled default prompts so the app never bricks offline.

## Free-tier impact
Langfuse only (see above); zero LLM quota.

## Effort estimate
M (half-day) incl. the offline-fallback path.

## Verdict
**GO**.
