# Feasibility: 001 - Scaffold backend, frontend, docker-compose

- **Issue**: #1 · **Phase**: 1 · **Date**: 2026-07-25 · **Author**: Claude (reviewed by Hanzala)

## Goal
Runnable skeleton of the whole system so every later issue lands on working rails: FastAPI with `/health` and `/readyz`, a React SPA shell, Qdrant container, pydantic-settings config, and tests for all of it.

## Approach options
1. **Vite React SPA + hand-written FastAPI app (chosen)** - the app is single-user on localhost with mic/WebSocket screens, so SSR buys nothing. The static build also lets FastAPI serve the frontend, which makes production one service on one origin: no CORS, same-origin WebSocket, one TLS endpoint.
2. Next.js - rejected. Its strengths (SSR, SEO, edge, ISR) do not apply here, and it needs a Node runtime beside FastAPI plus a reverse proxy to share an origin. Static export would forbid the server rendering that justifies it.
3. Cookiecutter FastAPI template - drags in auth, Celery and other extras we would delete.

## Risks & unknowns
- Windows + Docker Desktop quirks (drive sharing for Qdrant volume) → use named volume, not bind mount.
- Node/Python version drift → pin in README (Python 3.12, Node 20+).
- Deployment beyond localhost: `getUserMedia` requires HTTPS, so any LAN or remote access needs TLS (Tailscale or Caddy) or the mic silently fails. The persistent voice WebSocket also rules out serverless hosts.

## Free-tier impact
None (no API calls).

## Effort estimate
M (half-day) - mostly boilerplate + verifying the three services talk to each other.

## Verdict
**GO** - zero unknowns of substance; prerequisite for everything.
