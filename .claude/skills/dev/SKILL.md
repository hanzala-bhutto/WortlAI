---
name: dev
description: Start the full WortlAI stack (Qdrant via docker compose, FastAPI backend, Next.js frontend) and health-check every service. Use when the user wants to run, start, or restart the app.
---

# /dev - start and verify the full stack

## Steps

1. **Docker services**: `docker compose up -d` from repo root. Wait for Qdrant: `curl http://localhost:6333/readyz` must return ok (retry up to 30s).
2. **Backend**: from `backend/`, start `uvicorn app.main:app --reload --port 8000` in the background. Health-check `GET http://localhost:8000/health` (must return `{"status":"ok"}` and report which LLM provider + keys are configured).
3. **Frontend**: from `frontend/`, `npm run dev` in the background. Health-check `http://localhost:3000` returns 200.
4. If a `.env` key is missing, say exactly which one and where to get it (Groq: console.groq.com, NIM: build.nvidia.com, Langfuse: cloud.langfuse.com). Do not start services that can't work.

## Report

One line per service: name, port, status (green/red), and for red services the exact error and fix. End with the URL to open: http://localhost:3000

## Notes

- Ports busy → find the stale process (`Get-NetTCPConnection -LocalPort 8000`) and report it; don't kill without asking.
- This skill only starts/checks; it never edits code or config.
