# WortlAI 🗣️🇩🇪

**A voice-first AI German trainer that works like a synthetic German workplace - not a course.**

WortlAI exists because the fastest documented German learners (people "picking it up in a month" at German-only workplaces) aren't using a secret trick: they get massive forced speaking volume, a no-English environment, whole-phrase absorption, and corrections from life instead of grammar drills. WortlAI engineers exactly those conditions around one learner, using free-tier AI services running locally.

## Why existing tools are slow

| Duolingo & friends | WortlAI |
|---|---|
| Tapping words, minutes of real output per hour | Voice-first: you *speak* German the entire session |
| Accuracy first, fluency someday | **Fluency first** - only communication-breaking errors flagged early |
| Forgets what you struggle with | Persistent learner model: FSRS spaced repetition + error log drive every next session |
| Isolated words | **Chunks** (Redemittel/collocations) - what research says actually drives oral fluency |
| Generic content | Grounded in *your* textbooks (RAG) and *your* real life (daily missions in your city) |

## What it does

- **Talk mode**: push-to-talk (or hands-free VAD) conversation with an AI tutor in German, calibrated slightly above your level. Groq Whisper hears you; natural German neural voices answer; sub-2s latency target.
- **Silent Corrector**: logs your errors during conversation without interrupting; debriefs you after. Errors become reviewable items.
- **Curriculum agent**: plans each session from your due FSRS items, error patterns, and scenario rotation (Bürgeramt, doctor, small talk - roleplay with stakes).
- **Review deck**: chunk-based spaced repetition (FSRS), graded from real conversation use, spoken or typed.
- **Shadowing & listening drills**: speed-ramped TTS dialogues, Whisper-scored shadowing.
- **Real-life missions**: daily task in your actual city, pre-drilled in the morning, debriefed at night.
- **Progress dashboard**: immersion hours vs your 2-month protocol target, CEFR trajectory, error trends.

## Architecture

Next.js frontend ↔ FastAPI backend. Agents orchestrated as a checkpointed **LangGraph** state graph; **LlamaIndex** ingestion of textbook PDFs into **Qdrant** (semantic search) plus a lexical knowledge graph in **SQLite** typed-edge tables; **py-fsrs** scheduling; **Langfuse** for traces, versioned prompts, and evals (incl. LLM-as-judge). LLMs and Whisper via **Groq** free tier with **NVIDIA NIM** fallback; voices via **edge-tts**.

Every design decision is documented with its evidence in [`CLAUDE.md`](CLAUDE.md) and [`docs/PLAN.md`](docs/PLAN.md).

## Setup

Prereqs: Python 3.12, Node 20+, Docker Desktop, free API keys from [Groq](https://console.groq.com), [NVIDIA NIM](https://build.nvidia.com), [Langfuse](https://langfuse.com).

```bash
cp .env.example .env        # fill in keys
docker compose up -d        # Qdrant
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload
cd frontend && npm install && npm run dev
```

Drop your own German course PDFs into `Deustch_Books/` (gitignored - copyrighted material never enters the repo) and run the ingestion pipeline.

## Roadmap

- **Phase 0** - Foundation: docs, skills, GitHub workflow ← *current*
- **Phase 1** - Voice conversation loop (MVP: speak, get spoken replies, error debrief)
- **Phase 2** - Memory: RAG ingestion, learner model, chunk-based FSRS
- **Phase 3** - Listening trainer, shadowing, assessment, full LLMOps
- **Phase 4** - Multi-voice roleplay, real-media (Tagesschau) comprehension, MCP server

## Engineering workflow

Milestones = phases; one issue per task with acceptance criteria and a feasibility report in `docs/feasibility/`; `feat/<issue#>-slug` branches; Conventional Commits; one PR per issue.

---

*Built by Hanzala Bhutto with Claude Code. Single-user, local-first, free-tier only.*
