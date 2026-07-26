# Feasibility: 008 - Langfuse tracing + prompt management

- **Issue**: #7 · **Phase**: 1 · **Date**: 2026-07-25 · **Author**: Claude (reviewed by Hanzala)

## Goal
Trace every agent/LLM call (session id, agent, model, latency, tokens); all prompts versioned in Langfuse Prompt Management, fetched at runtime with cache fallback - from Phase 1 so history exists from day one.

## Approach options
1. **Langfuse Python SDK decorators + prompt client with local disk cache, against the local self-hosted instance (chosen)** - first-party, minimal code; cache keeps the app working when Langfuse is down.
2. Langfuse cloud free tier - rejected: data must stay on this machine.
3. A second, WortlAI-owned Langfuse stack - rejected: duplicates Postgres + ClickHouse + Redis + MinIO for no isolation gain over option 1.

## Hosting
Self-hosted. A Langfuse v3 compose stack (web, worker, postgres 17, clickhouse, redis, minio) runs locally under a sibling project at `ApplySync/langfuse/`, on `localhost:3000`. WortlAI uses it via a dedicated Langfuse project with its own key pair; traces, prompt versions, datasets and scores are project-scoped.

WortlAI ships no Langfuse infrastructure - the coupling is `LANGFUSE_BASE_URL` + keys in `.env`. The WortlAI frontend runs on 3001, since Langfuse web holds 3000.

## Risks & unknowns
- The instance belongs to another project: if it stops, tracing goes dark. Tracing must be non-fatal, and prompts need the disk cache + bundled fallback so sessions still run.
- Prompt fetch at runtime is a boot-time dependency unless cached - the cache is mandatory, not an optimisation.
- Open: whether the stack should move to a neutral shared-infra folder.

## Free-tier impact
None - self-hosted, zero third-party quota and zero LLM quota.

## Effort estimate
M (half-day) incl. the offline-fallback path.

## Verdict
**GO**.
