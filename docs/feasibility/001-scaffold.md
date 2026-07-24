# Feasibility: 001 — Scaffold backend, frontend, docker-compose

- **Issue**: #1 · **Phase**: 1 · **Date**: 2026-07-25 · **Author**: Claude (reviewed by Hanzala)

## Goal
Runnable skeleton of the whole system so every later issue lands on working rails: FastAPI with /health, Next.js shell, Qdrant container, pydantic-settings config.

## Approach options
1. **Manual scaffold following docs/PLAN.md layout (chosen)** — full control, no template cruft, matches our documented structure exactly.
2. `create-next-app` + cookiecutter FastAPI template — faster start but drags in opinionated extras we'd delete (auth, Celery, etc.).

## Risks & unknowns
- Windows + Docker Desktop quirks (drive sharing for Qdrant volume) → use named volume, not bind mount.
- Node/Python version drift → pin in README (Python 3.12, Node 20+).

## Free-tier impact
None (no API calls).

## Effort estimate
M (half-day) — mostly boilerplate + verifying the three services talk to each other.

## Verdict
**GO** — zero unknowns of substance; prerequisite for everything.
