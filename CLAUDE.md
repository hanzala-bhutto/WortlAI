# WortlAI — CLAUDE.md

AI-powered German fluency trainer: voice-first, multi-agent, free-tier-only, local single-user app.
Goal: take Hanzala (A2, living in Dresden since Oct 2024) to confident conversational German (~B1/B2 speaking) in ~2 months via a "synthetic German workplace" — forced spoken output, chunk-based FSRS memory, staged corrections.

Full plan: `docs/PLAN.md` (canonical). Read it before large changes.

## Architecture

```
Next.js frontend (talk / review / dashboard)
        │ REST + WebSocket (audio, streaming transcript)
FastAPI backend
 ├─ voice: Groq Whisper STT → agents → edge-tts (German neural voices)
 ├─ agents (LangGraph session graph): Tutor, Corrector (async), Curriculum, Assessment
 ├─ rag: LlamaIndex ingestion → Qdrant (semantic) + SQLite lexical graph (typed edges)
 ├─ learner: SQLite + SQLAlchemy — FSRS states (py-fsrs), error log, sessions
 └─ LLMOps: Langfuse — traces, versioned prompts, gold datasets, evals
```

## Stack decisions (audited — do not re-litigate without new evidence)

- **LLM**: Groq `openai/gpt-oss-120b` primary, `llama-3.3-70b-versatile` secondary, NVIDIA NIM fallback. All free tier; provider abstraction in `backend/app/llm/provider.py`.
- **STT**: Groq `whisper-large-v3-turbo` (Whisper is STT-only; it cannot speak).
- **TTS**: `edge-tts` (`de-DE-KatjaNeural`, `de-DE-ConradNeural`). Unofficial endpoint; fallback is Qwen3-TTS (Kokoro is English-only — not an option).
- **Qdrant** kept for mature free LlamaIndex integration, NOT performance — corpus is <10k vectors; sqlite-vec is the documented minimal alternative.
- **NO Neo4j**: evidence audit 2026-07-25 — all graph queries are 1–2 hops over <10k nodes; plain SQL joins suffice. GraphRAG research gains apply to multi-hop QA over large corpora, not our filtered retrieval. Lexical graph = SQLite typed-edge tables (`word_links`, `error_pattern_links`); exports to Neo4j in an afternoon if ever needed.
- **LangGraph** for session orchestration (checkpointed state graph: setup → converse ⇄ async-correct → debrief), **LlamaIndex** for all ingestion/retrieval. CrewAI/AutoGen/Vertex AI rejected (wrong shape / paid GCP).
- **No model training / no custom PyTorch code.** py-fsrs is pure-Python scheduling; torch appears only as sentence-transformers runtime. FSRS optimizer: only after 2–3 months of review data.

## Pedagogy rules (why the app is shaped this way)

- **Fluency before accuracy**: Corrector flags only communication-breaking errors in weeks 1–3; precision errors phase in later. Never interrupt mid-conversation — errors go to the post-session debrief.
- **Chunks, not words**: FSRS cards are phrases/Redemittel ("Ich hätte gern…"), not isolated vocabulary.
- **FSRS grades come from conversation**, not flashcard taps (used unprompted → Good/Easy; needed gloss → Hard; failed → Again). spaCy `de_core_news_sm` lemmatizes transcripts to detect tracked words.
- **Hours are the primary metric**: dashboard tracks combined immersion hours (app + wife-call segments + missions) vs the 2-month protocol target (~3 hrs/day).
- Tutor prompts must pin: "reply ONLY in German at CEFR level {X}".

## Conventions

- **Prompts live in Langfuse Prompt Management** (versioned, `production`/`staging`), never hardcoded in source.
- **Structured outputs are Pydantic-validated** (Corrector/Curriculum return typed JSON).
- **Every external dependency behind an abstraction**: `llm/provider.py`, `voice/tts.py`, `voice/stt.py`, `rag/embedder.py`. Swapping a tool must stay a one-file change.
- **LLM-extracted lexical-graph edges must cite an example sentence or be dropped** (anti-hallucination rule; `/graph-check` audits this).
- Python 3.12, FastAPI, SQLAlchemy; TypeScript, Next.js App Router, Tailwind, shadcn/ui.

## Engineering workflow (GitHub)

- Public repo `WortlAI`. Milestones = Phases 0–4. One issue per task (acceptance criteria, labels `phase:N` + `area:*`).
- **Feasibility report required before implementing any issue**: `docs/feasibility/NNN-slug.md` (goal, approaches, risks, free-tier impact, estimate, go/no-go).
- Branches `feat/<issue#>-slug` etc.; Conventional Commits; one PR per issue (`Closes #N`).
- **Claude opens + reviews PRs; only Hanzala merges.**
- **NEVER commit `Deustch_Books/` or `backend/data/`** — copyrighted Klett PDFs and derived content must not reach the public repo. `.gitignore` enforces this; do not weaken it.

## Running

- `docker compose up -d` (Qdrant) · backend: `uvicorn app.main:app --reload` from `backend/` · frontend: `npm run dev` from `frontend/`.
- Env: copy `.env.example` → `.env` (GROQ_API_KEY, NIM_API_KEY, LANGFUSE_* keys).
- Skills: `/dev` (start+healthcheck stack), `/ingest`, `/eval`, `/new-scenario`, `/graph-check`, `/progress`.

## Phase status

- Phase 0 (foundation): ✅ done 2026-07-25 (Project board pending token scope)
- Phase 1 (voice conversation loop): not started — issues #1–#8 ready, feasibility reports in docs/feasibility/
- Phase 2 (RAG + learner model + FSRS): not started
- Phase 3 (listening + assessment + LLMOps): not started
- Phase 4 (advanced): not started
