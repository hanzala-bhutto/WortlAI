# WortlAI — AI-Powered German Fluency Trainer (A2 → C1)

## Context

Hanzala has been in Germany since Oct 2024 and is stuck around A2. Existing tools (Duolingo etc.) are slow and forgetful: they don't force speaking output, don't model what the learner actually knows/forgets, and don't simulate real German conversation. Goal: an interactive, voice-first AI tutor that compresses the path to C1-level **listening and speaking** using free-tier AI services (Groq, NVIDIA NIM, Qdrant), RAG over user-supplied vocab PDFs, and a multi-agent architecture.

**Core thesis:** speed comes from (1) massive comprehensible input slightly above current level, (2) forced spoken output every session, (3) a persistent learner model with FSRS spaced repetition so nothing taught is ever forgotten, and (4) corrections delivered as post-session debriefs, not mid-conversation interruptions.

Runs **locally on Windows**, single user, free tier only.

## Architecture Overview

```
┌─────────────────────────────  Next.js frontend (localhost:3000) ─────────────────────────────┐
│  Talk mode (push-to-talk mic, live transcript, audio player)  │  Review deck  │  Dashboard   │
└───────────────────────────────────────┬──────────────────────────────────────────────────────┘
                                        │ REST + WebSocket (audio/transcript streaming)
┌───────────────────────────────────────▼─────────────  FastAPI backend (localhost:8000) ──────┐
│  Voice pipeline:  mic audio → Groq Whisper (STT) → agents → Edge TTS (German voice out)      │
│                                                                                              │
│  Agents (LLM = Groq llama-3.3-70b free tier; NVIDIA NIM as fallback provider):               │
│   • Tutor agent       — German conversation/roleplay, calibrated to level (i+1)              │
│   • Corrector agent   — analyzes each user utterance async, logs errors, post-session debrief│
│   • Curriculum agent  — plans next session: scenario + due FSRS vocab + weak grammar         │
│   • Assessment agent  — periodic CEFR estimate from conversation history                     │
│                                                                                              │
│  RAG: PDF ingest → chunk → embeddings → Qdrant (local Docker or free cloud 1GB)              │
│  Learner model: SQLite — vocab states (FSRS), error log, session history, level estimate     │
│  LLMOps: Langfuse free tier — traces every agent call, eval correction quality               │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js 15 (App Router) + TypeScript + Tailwind + shadcn/ui | Proper mic/audio UX, chosen by user |
| Backend | FastAPI (Python 3.12) + uvicorn | AI ecosystem, WebSocket support |
| STT | **Groq Whisper** `whisper-large-v3-turbo` | Free tier, fast, excellent German |
| LLM | **Groq** `openai/gpt-oss-120b` (primary — Groq's current flagship, strong multilingual/reasoning; verified July 2026), `llama-3.3-70b-versatile` (secondary), **NVIDIA NIM** (fallback provider) | All free tier; provider abstraction layer so any works. Groq free tier: ~1,000 req/day on large models, 2,000 Whisper audio req/day — ample for one user |
| TTS | **edge-tts** Python package (`de-DE-KatjaNeural` / `de-DE-ConradNeural`) | Free, natural German neural voices |
| Vector DB | **Qdrant** (Docker locally; free cloud as option) | Kept on integration grounds after evidence audit: free, one container, first-class LlamaIndex support. Technically oversized for our <10k vectors — sqlite-vec is the documented minimal alternative |
| Lexical graph | **Typed-edge SQLite tables** (`word_links`: from, to, edge_type ∈ family/collocation/government/synonym) | Evidence audit (2026-07-25): all planned graph queries are 1–2 hops over <10k nodes — plain JOINs suffice; GraphRAG research gains apply to multi-hop QA over large corpora, not our filtered retrieval. Neo4j rejected as unmeasured-need complexity; edge table exports to Neo4j in an afternoon if deep graph needs ever appear |
| RAG framework | **LlamaIndex** (ingestion, indexing, Qdrant integration) | Best-in-class data layer; hybrid with LangGraph orchestration (user choice) |
| Embeddings | `intfloat/multilingual-e5-large` via sentence-transformers (local, free) or NIM embeddings API | Strong German retrieval |
| Learner DB | SQLite + SQLAlchemy | Single user, zero ops |
| Spaced repetition | `fsrs` Python package (py-fsrs) | Modern scheduler, beats SM-2; pure Python, no training |
| NLP (German) | spaCy `de_core_news_sm` | Lemmatization for vocab detection from transcripts |
| Voice activity detection | Silero VAD (browser-side or backend) | Hands-free conversation mode |
| Agent orchestration | **LangGraph** (session as a state graph: warmup → converse ⇄ async-correct → debrief → schedule, with checkpointing so sessions pause/resume) + **Pydantic AI**-style typed structured outputs (validated JSON from Corrector/Curriculum) | User goal is fluency *and* learning the 2026 AI stack; LangGraph's checkpointing/interrupts genuinely fit the session shape. Rejected: CrewAI/AutoGen (free-delegation shape, wrong fit), Vertex AI (paid GCP platform, breaks local/free constraints) |
| Observability | Langfuse (free cloud tier) | Traces, prompt versioning, evals |

API keys needed (all free): Groq, NVIDIA NIM (build.nvidia.com), optionally Qdrant Cloud + Langfuse.

## Repository Layout

```
WortlAI/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app, CORS, routers
│   │   ├── config.py               # pydantic-settings, .env keys
│   │   ├── api/                    # routers: session, voice(ws), review, ingest, progress
│   │   ├── agents/                 # tutor.py, corrector.py, curriculum.py, assessor.py, prompts/
│   │   ├── llm/                    # provider.py (Groq/NIM abstraction, retry+fallback)
│   │   ├── voice/                  # stt.py (Groq Whisper), tts.py (edge-tts)
│   │   ├── rag/                    # LlamaIndex pipelines: glossary_parser.py, vision_extract.py, embedder.py, qdrant_store.py, lexical_graph.py (SQLite edges), retrieve.py
│   │   ├── learner/                # models.py (SQLAlchemy), fsrs_engine.py, error_log.py
│   │   └── db.py
│   ├── data/                       # SQLite file, dropped PDFs
│   └── requirements.txt
├── frontend/                       # Next.js app
│   └── src/app/                    # talk/, review/, dashboard/, api client, audio hooks
├── docker-compose.yml              # Qdrant
└── README.md
```

## The Core Loop (Talk Mode) — what a session looks like

1. **Session start**: Curriculum agent reads learner DB → picks scenario (e.g. "Termin beim Bürgeramt"), pulls 8–12 due FSRS words + 2 weak grammar patterns, retrieves matching vocab from Qdrant → builds the Tutor's system prompt ("weave these words in naturally; speak at level X; introduce ~3 new words").
2. **Conversation**: push-to-talk → audio → Groq Whisper → text shown live → Tutor replies in German (streamed) → Edge TTS audio autoplays. Adjustable speech rate (slower early, native speed later). "Wie bitte?" button replays slower + shows transcript.
3. **Silent correction**: every user utterance is sent async to the Corrector agent, which logs errors (type, severity, the correct form) without interrupting flow.
4. **Debrief**: session ends → Corrector presents top errors with explanations → each error becomes a reviewable item; each vocab word encountered gets its FSRS state updated (used correctly = good grade, needed help = poor grade).
5. **Scheduling**: FSRS computes next-due dates; Curriculum agent uses them for the next session. Assessment agent re-estimates CEFR level every ~10 sessions and moves the difficulty target.

Listening-specific training (the weakest skill for most learners): a **Listening drill mode** where TTS reads level-appropriate passages/dialogues (optionally two voices), user answers comprehension questions or shadows the sentence, speed ramps from 0.8× to 1.1×.

## RAG + Knowledge Graph Design (informed by inspecting `Deustch_Books/`)

**Actual source material** (inspected 2026-07-25):
- `NWn_A1_Glossar_Deutsch-Englisch.pdf` + `NWn_A2_Glossar_Englisch1.pdf` — **goldmine**: Netzwerk neu chapter glossaries with per-word article, plural form (`die Messe, -n`), irregular verb forms (`aus-schlafen, er schläft aus, hat ausgeschlafen`), example sentences, English translations, chapter/topic grouping, word-stress marks. Parse **deterministically into structured word records**, not naive text chunks.
- `Netzwerk Neu A2 Kursbuch.pdf` + `A2 Ubungbuch.pdf` — image-heavy textbook pages. The **Redemittel boxes** (speech-act phrase sets: etwas vorschlagen / zusagen / absagen / nachfragen) are exactly the chunks for chunk-based FSRS; grammar boxes (e.g. weil-Nebensatz) feed the grammar collection. Needs **vision-model extraction** (page image → LLM with vision via NIM free tier) rather than plain text extraction — batch offline job, one-time per book.
- `Adjektiv-Endungen.pdf` — grammar reference → grammar collection.
- **Seed extra**: Goethe-Institut A1/A2/B1 wordlists (free PDFs) → objective per-level vocab-coverage metric for the Assessment agent.

**Storage — two stores, decided after an evidence audit (2026-07-25; workload: <10k vectors, <10k graph nodes, 1–2-hop queries, single user):**
1. **Qdrant** (semantic search — "what content means like X"): collections `vocab` (one point per word/chunk, payload `{level, topic, chapter, type}`) and `content` (grammar explanations, dialogues, Redemittel sets). Built via **LlamaIndex** ingestion pipeline. Kept for its mature free LlamaIndex integration, not performance (sqlite-vec would suffice at this scale and remains the minimal fallback).
2. **SQLite** (learner state + lexical graph): FSRS states, error log, sessions — **plus** the knowledge graph as typed-edge tables: `word_links(from_word, to_word, edge_type ∈ IN_FAMILY | COLLOCATES_WITH | GOVERNS | SYNONYM | ANTONYM, citation)` and `error_pattern_links` (ErrorPattern → affected words/chunks/rules). Structural facts (gender, plural, chapter/topic) come **deterministically from the glossary parse**; relational edges via **LLM extraction with a validation pass** (every LLM edge must cite an example sentence or is dropped — guards against hallucinated edges). All planned curriculum walks are 1–2-hop JOINs/recursive CTEs (word families, sein-verbs, error-pattern fan-out) — milliseconds at this scale.

**Neo4j: explicitly rejected** (GraphRAG research gains apply to multi-hop QA over large corpora, not our filtered retrieval; our graph ops need no graph engine; JVM container + Cypher + third sync target = unmeasured-need complexity). **Upgrade path**: the edge tables export to Neo4j in an afternoon if deep traversal/visualization needs ever materialize.

**Who queries what:** Curriculum agent → SQL graph walks ("he just learned *ziehen* → teach *umziehen*, *der Umzug* next; his `zwei-Wege-Präpositionen` ErrorPattern touches these 12 chunks") + Qdrant topic search. Tutor → Qdrant Redemittel/phrases for the scenario. Corrector → grammar collection for grounded explanations, writes ErrorPattern links back into SQLite. Assessment → coverage vs Goethe lists.

## Interactivity Principles (what makes this feel alive, not a chatbot with a mic button)

- **Latency is the product**: sentence-level TTS pipelining — speak the tutor's first sentence while the rest streams from the LLM; target < 2s to first audio. Word-by-word transcript streaming.
- **Hands-free mode**: Silero VAD (voice activity detection) detects end of speech — no push-to-talk needed; feels like a phone call. (Push-to-talk stays as the reliable fallback.)
- **Tappable transcript**: click any German word → instant gloss + one-tap "add to my FSRS deck".
- **Scenarios with stakes**: goal-based roleplay (get the Termin, negotiate the Kaution, handle the grumpy Beamter) with pass/fail outcome and score; different TTS voices = different personas.
- **"Wie bitte?" button**: replay slower + reveal transcript — trains listening rather than punishing it.
- **Live level dial**: user adjusts difficulty mid-conversation; tutor visibly adapts vocabulary/speed.
- **15-min structured daily session**: 3-min spoken warm-up on due vocab → 10-min scenario → 2-min debrief; streaks + weekly CEFR trajectory chart for visible progress.

## What the research says about "fluent in 1–2 months" people (and what it means for the app)

Researched: Benny Lewis's 3-month German mission (prior dormant school German → attempted C2 after 3 months in Berlin, no-English rule), a documented 0→C1 immersion journey (monoglotanxiety.com — comprehensible input as foundation, output from B1), Refold-method logs, and SLA research on shadowing and formulaic sequences. How workplace "1-month" people actually do it:

1. **Volume, compressed**: 6–10 hrs/day of forced German ≈ 200–300 hrs/month. FSI-scale data puts German professional proficiency at ~750 hrs — fast learners compress hours, they don't skip them. One month in a German-only workplace ≈ a year of casual app use.
2. **Forced output with real stakes** — they must speak to function; no opt-out button.
3. **No-English environment.**
4. **Dormant base reactivation** — most had school German that "wakes up" (Hanzala's A2 = exactly this situation).
5. **Fluency before accuracy** — they speak confidently with many errors and get corrected by life; classic apps/courses invert this (accuracy-first) and that's *why* they're slow.
6. **Chunks, not words** — they absorb whole workplace phrases repeated daily; SLA research confirms formulaic sequences are the main driver of oral fluency.
7. **Massive repeated listening** of the same routines — effectively natural shadowing; controlled studies show shadowing significantly improves oral fluency and listening comprehension.

**Design consequence: the app is a synthetic German workplace, not a course.** Features this adds/changes:

- **Hours are the primary metric.** Dashboard tracks daily immersion hours against a 2-month protocol target (~3–4 focused hrs/day ≈ 200 hrs). Honest by design: it shows the user when their pace implies 6 months, not 2.
- **German-only mode**: above a toggle, the app contains no English — glosses are simpler German first (like a patient colleague), English only on second tap.
- **Chunk-based FSRS**: cards are phrases/collocations ("Ich hätte gern…", "Es kommt darauf an"), not isolated words; single words only where necessary.
- **Fluency islands**: 10–15 personal scripts (self-intro, your work, your week, your opinion patterns) drilled to full automaticity — the confidence backbone of every real conversation.
- **Shadowing mode** as a first-class daily drill: TTS plays a sentence, user speaks along/immediately after, Whisper transcript-diff scores it; speed ramps to native.
- **Daily real-life mission**: the app assigns one real task in Germany ("order at the Bäckerei without pointing", "ask a colleague about their weekend"), pre-drills the chunks for it, then debriefs it in the evening session. The city is the classroom; the app is the coach.
- **No-opt-out pressure**: scheduled surprise "calls" — a notification starts a 3–5 min spontaneous German conversation, simulating workplace unpredictability.
- **Staged correction policy**: weeks 1–3 the Corrector flags only communication-breaking errors (fluency phase); precision errors (adjective endings, etc.) phase in later — matching how workplace learners actually progress.

## Real-Life Integration Layer (Hanzala's actual immersion sources)

Current forced-German exposure is near zero (English office/uni in Dresden; only market phrases). The app must manufacture immersion from three real assets:

1. **Daily phone calls with wife (B2 German, in Pakistan, 30 min–2 hrs/day)** — the highest-value asset. Phone = pure listening/speaking (no gestures), the exact skill gap. Protocol: a German-only segment in every call, starting at 15–20 min and growing.
   - **Pre-call prep flow** (app feature): daily drill = "tonight, tell her X/Y/Z in German" — rehearse the chunks, AI roleplays her, then deploy live on the real call.
   - **Post-call debrief** (app feature): 2-min spoken debrief in German; stuck words/errors → FSRS deck + error log. Wife needs no app access (single-user, local); she can text errors she notices and user logs them in one tap.
2. **Dresden errands** — escalating weekly real-world missions (ask where something is → ask for a recommendation at the Theke → small talk in the queue), pre-drilled by the app, debriefed after.
3. **Dead time** (commute, gym, cooking) — shadowing + listening drills fill it.

Target math without quitting the job: app sessions 1–1.5 hr + call segment 0.25–0.5 hr + dead-time listening ~1 hr + errands ≈ **~3 hrs/day ≈ 180 hrs in 2 months** — inside the documented fast-learner range. The dashboard tracks this combined immersion time, not just in-app time (one-tap logging for call segments and missions).

## Expectations (honest)

A2 + living in Germany + dormant base + 3–4 focused hrs/day with this system = **confidently conversational (functional B1/B2 speaking) in ~2 months is aggressive but consistent with the documented cases**. C1 *listening* follows with continued real-media input; C1 *accuracy* takes longer for everyone. At 45–60 min/day instead, expect ~3–4 months to conversational B1. No tool deletes the hours — this one compresses them and makes each count.

## Build Phases

## Engineering Workflow (GitHub — decided with user)

- **Repo**: new **public** GitHub repo `WortlAI` (created via `gh`; user authenticates if needed). **Hard rule**: `Deustch_Books/` and `backend/data/` gitignored from commit #1 — copyrighted Klett PDFs and content extracted from them must never reach the public repo.
- **Milestones = Phases 0–4**; **GitHub Project board** "WortlAI" (Backlog → In Progress → In Review → Done).
- **One issue per independent task**: user story, verifiable acceptance criteria, technical notes; labels `phase:N` + `area:{backend,frontend,rag,agents,voice,llmops,docs}`.
- **Full ceremony (user's choice)**: every issue gets a short templated **feasibility report** in `docs/feasibility/NNN-slug.md` (goal, approach options, risks, free-tier impact, effort estimate, go/no-go) written *before* implementation.
- **Branches**: `feat/<issue#>-slug`, `fix/…`, `docs/…`, `chore/…`. **Conventional Commits**. **One PR per issue**, body `Closes #N`.
- **Review flow**: Claude opens the PR and runs a code review on it; **user reads the review summary and merges**. Claude never merges.

### Phase 0 — Project Foundation (first thing executed after plan approval)
1. **`CLAUDE.md`** at repo root: project purpose, the audited stack decisions *with their reasons* (esp. why no Neo4j, why Qdrant despite being oversized, fluency-before-accuracy pedagogy), architecture map, conventions (provider abstractions, prompts live in Langfuse not code, structured outputs Pydantic-validated), how to run/test, phase status.
2. **`README.md`**: project vision (synthetic German immersion, A2→conversational in 2 months), the research basis, feature overview, setup instructions (API keys needed: Groq, NIM, Langfuse; docker compose; env template), phase roadmap.
3. **Claude Code skills** in `.claude/skills/`: create `/dev`, `/ingest`, `/eval`, `/new-scenario`, `/graph-check`, `/progress` as skill definitions now (each with steps + verification), even where the underlying feature lands in a later phase — the skill file documents the intended workflow and gets wired as its feature ships.
4. **Persistent memory**: save project memories (stack decisions + reasons, Hanzala's learner profile: A2, Dresden, English office/uni, wife B2 on daily calls from Pakistan, 2-month conversational goal, ~3 hrs/day protocol) so every future session starts with full context.
5. `git init` + `.gitignore` **first** (node_modules, .env, `Deustch_Books/`, `backend/data/`, __pycache__) + `.env.example` + first commit; create public GitHub repo `WortlAI` via `gh`, push, create Milestones (Phase 0–4), Project board, labels, and the Phase 0–1 issues with their feasibility reports (`docs/feasibility/` template established here).

### Phase 1 — Voice Conversation Loop (the MVP; ~the first thing we build)
Goal: speak German with an AI tutor and get a post-session error debrief. Usable for daily practice immediately.
1. Scaffold repo: FastAPI backend + Next.js frontend + docker-compose (Qdrant, unused yet).
2. `llm/provider.py`: Groq client with NIM fallback, streaming, retry.
3. Voice pipeline: WebSocket route; browser MediaRecorder → Groq Whisper STT; edge-tts → audio back to browser.
4. Session as a **LangGraph graph** from day 1 (nodes: scenario-setup → converse ⇄ async-correct → debrief; SQLite checkpointer for pause/resume). Tutor agent v1: fixed-level German conversation with scenario picker (5–6 hardcoded scenarios), streamed replies. Corrector output as Pydantic-validated structured JSON.
5. Corrector agent v1: async per-utterance analysis → JSON error log → end-of-session debrief screen. Staged policy from day 1: communication-breaking errors only at first.
6. Frontend Talk page: push-to-talk button, live transcript (German + toggleable gloss; German-only mode default), audio autoplay, speech-rate slider, debrief view, session hours tracking.
7. SQLite session + error persistence.

**Phase 1 verification**: run backend (`uvicorn`) + frontend (`npm run dev`), hold a full spoken conversation in German, confirm transcript accuracy, spoken replies, and a debrief listing real errors from the session.

### Phase 2 — Memory: RAG + Learner Model + FSRS

**No model training / no custom PyTorch code in this phase.** Components:

1. **Ingestion via LlamaIndex** (see RAG + KG Design above): deterministic glossary parser → structured word records; vision-model extraction for Kursbuch Redemittel/grammar boxes; embeddings via `sentence-transformers` `multilingual-e5-large` (torch runtime, CPU — we write no torch code) **or** NIM embeddings API. Embedder behind `rag/embedder.py`. Load Qdrant collections + build SQLite lexical-graph edge tables (deterministic edges from glossary parse; LLM-extracted relational edges with citation-validation pass). Ingest user PDFs + Goethe A1–B1 wordlists.
2. **FSRS engine over chunks** (cards = phrases/collocations + fluency-island scripts, single words only where needed) (`py-fsrs`, pure Python — a ~20-parameter memory-scheduling algorithm, not deep learning): each word = a card with stability/difficulty/due-date state. **Grades come from conversation, not flashcard taps**: used correctly unprompted → Good/Easy; needed the gloss → Hard; failed/avoided → Again. (py-fsrs's optional torch-based optimizer that personalizes parameters on your review history: enable only after ~2–3 months of data.)
3. **Vocab detection via spaCy** (`de_core_news_sm`): lemmatize each session transcript so all inflections (ging/gegangen/gehe → gehen) map to tracked lemmas; auto-update FSRS states from free conversation.
4. **Learner DB schema** (SQLite): `words` (lemma, level, translation, source), `word_states` (FSRS fields), `error_log` (German-specific taxonomy: gender/article, case endings, verb-second/verb-final position, Perfekt auxiliary choice, preposition+case, word order), `sessions`.
5. **Curriculum agent**: session brief = (due FSRS words ∩ scenario topic, via Qdrant filter) + top recurring error types + scenario rotation.
6. **Review deck page**: fast review of due vocab and past errors, typed **or spoken** answers (Whisper-checked), FSRS grading.

### Phase 3 — Listening Trainer + Assessment + Immersion Protocol
1. Shadowing mode (TTS sentence → speak along → Whisper transcript-diff score, speed ramp 0.8×→1.1×) + listening drills (TTS dialogues, comprehension Qs).
2. Daily real-life mission generator (pre-drill chunks morning, debrief evening) + surprise-call notifications.
3. Assessment agent: CEFR estimation from error rates, vocab coverage vs Goethe lists, utterance complexity; dashboard tracking **immersion hours vs 2-month protocol target**, level trajectory, streak, error-type trends.
3. Full LLMOps buildout (see LLMOps section below).

### Phase 4 — Advanced (only if Phases 1–3 prove out)
- Multi-voice roleplay (two TTS voices, e.g. simulated group conversation / eavesdropping drills for listening).
- Real-media comprehension: ingest transcripts of Tagesschau/Easy German via yt-dlp for authentic listening at B2/C1.
- MCP server exposing the learner model (so any Claude/LLM client can quiz you); fine-tuning a small model on your error corpus — explicitly deferred: low ROI vs prompt+RAG until there's months of data.

## LLMOps Design (Langfuse-centered)

1. **Traces**: every agent call (Tutor turn, Corrector analysis, Curriculum brief, ingestion extraction) traced in Langfuse with session id, agent name, model, latency, token cost. Debug any bad output by opening its exact trace (prompt + retrieved context).
2. **Prompt versioning**: all prompts in Langfuse Prompt Management (versioned, `production`/`staging` labels), fetched at runtime — never hardcoded. Introduced from Phase 1 so history exists from day one.
3. **Gold datasets** (~50–100 items per task, grown from real usage): Corrector — real learner utterances with labeled expected errors ("Ich habe gegangen" → [Perfekt auxiliary: sein]); harvested from Hanzala's own sessions, labels verifiable with wife (B2). Tutor — transcripts labeled for CEFR level-appropriateness.
4. **Evals**: offline harness (Langfuse datasets/runs) on every prompt change. Corrector: deterministic precision/recall vs gold labels (missed errors + hallucinated errors). Tutor: **LLM-as-judge** with rubric (CEFR-appropriate? German-only? natural?), judged by a stronger/different model than the one under test. Metric deltas gate prompt promotion to `production`.
5. **Online feedback flywheel**: 👍/👎 on corrections in debrief UI → Langfuse scores → weekly triage; bad cases graduate into the gold dataset.

## Claude Code Skills (`.claude/skills/` — automate repeated project work)

- **/ingest** — re-run full pipeline on `Deustch_Books/`: glossary parse, vision extraction, rebuild Qdrant + Neo4j; print stats (words, edges, orphans).
- **/dev** — start full stack (docker compose: Qdrant, Neo4j, Langfuse; uvicorn; next dev), health-check all services.
- **/eval** — run eval suite vs gold datasets, compare to baseline, report metric deltas per prompt version.
- **/new-scenario** — scaffold a roleplay scenario: system prompt, Redemittel links, graph topic edges, scenario-registry entry.
- **/graph-check** — lexical-graph quality audit (SQLite edge tables): orphan words, uncited LLM edges, sample edges for human review.
- **/progress** — learner report from SQLite: immersion hours vs 2-month target, FSRS due counts, error-type trends.

Create `/dev` and `/ingest` during Phase 1–2 as their subjects come to exist; the rest as their features land.

## Key Design Decisions (already made with user)
- Stack: Next.js + FastAPI. TTS: Edge TTS (Whisper is STT-only — it cannot speak). STT/LLM: Groq free tier, NIM fallback. Local-only deployment, single user, no auth. Build order: Phase 1 voice loop first.

## Risks / Notes
- Groq free tier rate limits (requests/min + audio-seconds/day) are the main constraint → provider fallback to NIM, and keep corrector calls batched.
- edge-tts is an unofficial endpoint; if it breaks or quality disappoints, fallback/upgrade is **Qwen3-TTS** (open-weights, multilingual incl. German, voice cloning — the 2026 open-TTS leader; Kokoro is English-only so not an option). The `voice/tts.py` interface hides the engine either way.
- Every external dependency sits behind an abstraction (`llm/provider.py`, `voice/tts.py`, `voice/stt.py`, `rag/embedder.py`) — swapping any tool for a better one later is a one-file change, by design.
- Llama's German is good but not perfect; prompts must pin "reply ONLY in German at CEFR level {X}" and the Corrector prompt needs few-shot German error examples. If quality disappoints, NIM offers other models to swap via the provider layer.
