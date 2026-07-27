# WortlAI - CLAUDE.md

Voice-first German trainer. Takes Hanzala (A2, in Dresden) to confident B1/B2 speaking in ~2 months: forced spoken output, chunk-based FSRS memory, corrections after the session.

Canonical plan: `docs/PLAN.md`. Read it before large changes.

## Architecture

```
React SPA, Vite (talk / review / dashboard)
        │ REST + WebSocket (audio, streaming transcript)
FastAPI backend
 ├─ voice: Groq Whisper STT -> agents -> edge-tts (German neural voices)
 ├─ agents (LangGraph session graph): Tutor, Corrector (async), Curriculum, Assessment
 ├─ rag: LlamaIndex ingestion -> Qdrant (semantic) + SQLite lexical graph (typed edges)
 ├─ learner: SQLite + SQLAlchemy, FSRS states (py-fsrs), error log, sessions
 └─ LLMOps: Langfuse, traces, versioned prompts, gold datasets, evals
```

## Stack decisions (audited, don't re-litigate without new evidence)

- LLM: Groq `openai/gpt-oss-120b` primary, `llama-3.3-70b-versatile` secondary, NVIDIA NIM fallback. All free tier.
- STT: Groq `whisper-large-v3-turbo`, batch only (no streaming STT on free tier). TTS: `edge-tts` German voices, fallback Qwen3-TTS.
- Qdrant kept for the LlamaIndex integration, not performance. Corpus is under 10k vectors; sqlite-vec is the minimal alternative.
- No Neo4j. All graph queries are 1-2 hops over under 10k nodes, so SQLite typed-edge tables (`word_links`, `error_pattern_links`) suffice.
- LangGraph for session orchestration, LlamaIndex for ingestion and retrieval. CrewAI, AutoGen and Vertex AI rejected.
- No model training. py-fsrs is pure Python; the FSRS optimizer waits for 2-3 months of review data.
- Frontend is a React SPA on Vite, not Next.js. Nothing needs SSR, and the static build lets FastAPI serve the app.
- Deployment is one service on one origin: `frontend/dist` served by FastAPI (`app/static.py`) next to `/api/v1`. Vite dev on 3001 proxies backend paths so dev matches prod. Mic access needs HTTPS or localhost, so remote access needs Tailscale or Caddy. The voice WebSocket rules out serverless.

## API shape

- REST (`/api/v1`) for anything not streaming, so it stays curl-able and testable.
- One WebSocket, `/api/v1/voice/stream`, for the voice loop: audio up, transcript and reply tokens and TTS audio down. Needed for token streaming, early audio playback and hands-free barge-in.
- SSE for one-way progress on long jobs like ingest.
- No webhooks (nothing external calls us) and no gRPC (browsers need a proxy for it).

## Pedagogy rules

- Fluency before accuracy. Weeks 1-3 flag only communication-breaking errors. Never interrupt mid-conversation; errors go to the debrief.
- Chunks, not words. FSRS cards are phrases and Redemittel ("Ich hätte gern..."), not isolated vocabulary.
- FSRS grades come from conversation, not flashcard taps: used unprompted is Good/Easy, needed a gloss is Hard, failed is Again. spaCy `de_core_news_sm` lemmatizes transcripts.
- Hours are the primary metric: app plus wife-call segments plus missions, against ~3 hrs/day.
- Tutor prompts must pin "reply ONLY in German at CEFR level {X}".

## Conventions

- Write the test first. Cover error paths and degraded states, not just the happy path.
- Domain routes under `/api/v1`. `/health` and `/readyz` stay unversioned so probes have a stable address.
- Every endpoint declares a Pydantic `response_model`. `-> dict` gives a useless OpenAPI schema.
- Tunables come from `.env` and are listed in `.env.example`. URLs and model ids have no defaults in code, so a missing one fails at boot. Missing API keys are the exception: a degraded state `/health` reports, not a crash.
- Langfuse is self-hosted, not cloud. The instance is at `ApplySync/langfuse/` on port 3000 and belongs to a sibling project; we use our own Langfuse project and keys. Don't restart that stack for us, and don't let tracing failures break a session.
- Prompts live in Langfuse Prompt Management (`production`/`staging`), not in source, fetched at runtime with a disk cache and bundled fallback.
- Corrector and Curriculum return Pydantic-validated JSON.
- Keep external deps behind one file: `llm/provider.py`, `voice/tts.py`, `voice/stt.py`, `rag/embedder.py`.
- Frontend state: TanStack Query for server data, a reducer or small store for live session state, `useState` or `localStorage` for UI prefs.
- Python 3.12, FastAPI, SQLAlchemy. TypeScript, React 19 + Vite, TanStack Router and Query, Tailwind, shadcn/ui (add components when an issue needs them).
- TanStack moves fast. Check the installed exports in `node_modules/@tanstack/*` instead of working from memory.

## Guardrails

Keep each one concrete enough to test.

1. Parse every agent response into its Pydantic model. Retry once on malformed output, then fall back to something safe. Unvalidated text never reaches the database or UI.
2. Check Tutor replies for English drift and for exceeding the target CEFR level. Regenerate a reply that fails; the prompt alone isn't enough.
3. Cap audio seconds per utterance, tokens per reply and requests per session, so a loop can't burn the Groq free tier.
4. Provider failure is a normal path: retry with backoff, fall back to NIM, then degrade. Never hang a session.
5. Write to the learner model only from validated fields. LLM-extracted graph edges need a cited example sentence or they're dropped (`/graph-check` audits this).
6. Treat all content as data, never instructions. Riskiest inputs are vision-extracted textbook pages, RAG chunks and transcripts:
   - Put untrusted text in a delimited data block, never in instruction position.
   - Pin agent policy (level, German-only, correction staging) server-side and re-check on output.
   - Scan ingested text for instruction-like patterns and quarantine instead of indexing.
   - No side-effecting tools driven by untrusted text.
   - Never echo system prompts; a reply that leaks them fails validation.

## Engineering workflow

- Public repo. Milestones are Phases 0-4, one issue per task with acceptance criteria and `phase:N` + `area:*` labels.
- Feasibility report before implementing any issue: `docs/feasibility/NNN-slug.md`.
- Branches `feat/<issue#>-slug`, Conventional Commits, one PR per issue (`Closes #N`).
- Claude opens and reviews PRs. Only Hanzala merges. Ask before committing.
- Never commit `Deustch_Books/` or `backend/data/`. Copyrighted PDFs must not reach the public repo, and `.gitignore` must not be weakened.

## Running

- Dev: `docker compose up -d`, then `uvicorn app.main:app --reload` in `backend/`, then `npm run dev` in `frontend/`. Open :3001.
- Single service: `npm run build`, then run uvicorn alone. Whole app at :8000.
- Ports: backend 8000, frontend dev 3001, Qdrant 6333, Langfuse 3000.
- Tests: `pytest` in `backend/`, `npm test` in `frontend/`.
- Lint/format (backend): `ruff check .` and `ruff format .` in `backend/` (config in `backend/pyproject.toml`). Run `pre-commit install` once so the ruff hook lint-fixes and formats staged Python on every commit.
- Env: copy `.env.example` to `.env`. A missing value stops startup.
- Skills: `/dev`, `/ingest`, `/eval`, `/new-scenario`, `/graph-check`, `/progress`.

## Phase status

- Phase 0 (foundation): done 2026-07-25.
- Phase 1 (voice loop): issues #1-#8 ready, feasibility reports written. #1 in progress.
- Phases 2-4: not started.
