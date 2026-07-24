# Feasibility: 007 — SQLite persistence (sessions, errors, hours)

- **Issue**: #6 · **Phase**: 1 · **Date**: 2026-07-25 · **Author**: Claude (reviewed by Hanzala)

## Goal
SQLAlchemy models for sessions, error_log, immersion-hours log (source: app|call|mission) + POST /log-hours for manual call/mission logging.

## Approach options
1. **SQLAlchemy + Alembic from day one (chosen)** — schema will evolve every phase; migrations from the start beat "recreate the DB" pain later.
2. Raw SQL / no migrations — simpler today, guaranteed regret in Phase 2 when FSRS + graph tables land.

## Risks & unknowns
- Two SQLite writers (app DB + LangGraph checkpointer) → separate files (`wortlai.db`, `checkpoints.db`), WAL mode; no shared-writer contention.

## Free-tier impact
None.

## Effort estimate
S (<2h).

## Verdict
**GO**.
