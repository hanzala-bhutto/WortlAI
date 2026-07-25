# WortlAI 🗣️🇩🇪

A voice-first AI German trainer that works like a synthetic German workplace, not a course.

The fastest German learners aren't using a trick. They get huge speaking volume, no English, whole phrases instead of single words, and corrections from real life. WortlAI builds those conditions around one learner using free-tier AI, running locally.

## Why it's different

| Duolingo and friends | WortlAI |
|---|---|
| Tapping words, minutes of output per hour | You speak German the whole session |
| Accuracy first | Fluency first, only communication-breaking errors flagged early |
| Forgets what you struggle with | FSRS spaced repetition plus an error log drive every next session |
| Isolated words | Chunks and Redemittel, what actually drives oral fluency |
| Generic content | Grounded in your textbooks and your real life |

## What it does

- **Talk mode**: push-to-talk or hands-free conversation with a tutor pitched just above your level. Groq Whisper listens, German neural voices answer.
- **Silent Corrector**: logs errors without interrupting, then debriefs you. Each error becomes reviewable.
- **Curriculum agent**: plans each session from due FSRS items, error patterns and scenario rotation (Bürgeramt, doctor, small talk).
- **Review deck**: chunk-based FSRS, graded from real conversation use.
- **Shadowing and listening drills**: speed-ramped TTS, Whisper-scored shadowing.
- **Daily missions**: a real task in your city, drilled in the morning, debriefed at night.
- **Dashboard**: immersion hours against the 2-month target, CEFR trajectory, error trends.

## Architecture

React SPA (Vite) and a FastAPI backend, served from one origin in production. LangGraph runs the session as a checkpointed state graph. LlamaIndex ingests textbook PDFs into Qdrant, with a lexical graph in SQLite typed-edge tables. py-fsrs schedules reviews. Langfuse (self-hosted) handles traces, versioned prompts and evals. LLMs and Whisper come from Groq's free tier with NVIDIA NIM as fallback; voices from edge-tts.

Decisions and their evidence live in [`CLAUDE.md`](CLAUDE.md) and [`docs/PLAN.md`](docs/PLAN.md).

## Setup

Needs Python 3.12, Node 20+, Docker Desktop, and free keys from [Groq](https://console.groq.com) and [NVIDIA NIM](https://build.nvidia.com).

Langfuse is self-hosted so traces stay on your machine. Start the official [compose stack](https://langfuse.com/self-hosting/docker-compose) (it uses port 3000), create a project called `WortlAI` inside it, and put that project's keys in `.env`. Sharing one instance across projects is fine, Langfuse scopes everything per project.

```bash
cp .env.example .env                 # fill in keys
docker compose up -d                 # Qdrant on :6333

cd backend
py -3.12 -m venv .venv               # python3.12 -m venv .venv elsewhere
.venv/Scripts/activate               # source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload        # :8000, API docs at /docs

cd ../frontend
npm install && npm run dev          # :3001, proxies backend paths
```

Open http://localhost:3001. The System panel shows whether the backend, Qdrant and each API key are actually live, so a half-configured setup is obvious.

Tests: `pytest` in `backend/`, `npm test` in `frontend/`.
Ports: frontend dev 3001, backend 8000, Qdrant 6333, Langfuse 3000.

## Running it as one service

`npm run build` writes `frontend/dist`, which FastAPI serves itself alongside the API and the voice WebSocket. Run uvicorn alone and the whole app is at http://localhost:8000: one origin, no CORS, one endpoint to put behind TLS.

Before running it anywhere but your own machine: microphone access needs HTTPS or `localhost`, so plain LAN HTTP means the mic silently fails. Put Tailscale or Caddy in front. The voice pipeline also holds a WebSocket open, so serverless hosts won't work.

Drop your own German course PDFs into `Deustch_Books/` (gitignored, copyrighted material never enters the repo) and run the ingestion pipeline.

## Roadmap

- **Phase 0** Foundation: docs, skills, workflow. Done.
- **Phase 1** Voice conversation loop. In progress.
- **Phase 2** Memory: RAG ingestion, learner model, chunk-based FSRS.
- **Phase 3** Listening, shadowing, assessment, full LLMOps.
- **Phase 4** Multi-voice roleplay, real media, MCP server.

## Engineering workflow

Milestones are phases. One issue per task with acceptance criteria and a feasibility report in `docs/feasibility/`, `feat/<issue#>-slug` branches, Conventional Commits, one PR per issue.

---

*Built by Hanzala Bhutto with Claude Code. Single user, local first, free tier only.*
