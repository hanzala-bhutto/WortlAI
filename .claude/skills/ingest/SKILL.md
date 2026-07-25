---
name: ingest
description: Run the full WortlAI ingestion pipeline over Deustch_Books/ - parse glossaries, vision-extract textbook pages, rebuild Qdrant collections and the SQLite lexical graph - and report stats. Use after adding/changing source PDFs or ingestion code.
---

# /ingest - rebuild knowledge stores from source PDFs

**Status: pipeline lands in Phase 2.** Until `backend/app/rag/` exists, report that and stop.

## Steps

1. Verify Qdrant is up (`curl http://localhost:6333/readyz`); if not, start it (`docker compose up -d`).
2. Run the pipeline from `backend/`: `python -m app.rag.ingest --source ../Deustch_Books --rebuild`
   - Glossary PDFs (NWn_*Glossar*) → deterministic parser → structured word records.
   - Kursbuch/Übungsbuch → vision extraction (NIM vision model) → Redemittel chunks + grammar boxes. Vision extraction is cached per page hash in `backend/data/vision_cache/` - only new/changed pages hit the API.
   - Goethe wordlists (if present in `backend/data/goethe/`) → level-coverage reference.
3. Rebuild: Qdrant collections `vocab` + `content`; SQLite `words`, `word_links`, chunk tables. Deterministic edges from parse; LLM relational edges must carry a citation (example sentence) or are dropped.

## Report

- Words parsed per source; chunks extracted; Qdrant points per collection; edges by type (deterministic vs LLM-extracted vs dropped-uncited); orphan words (no edges, no topic).
- Diff vs previous run if stats file `backend/data/ingest_stats.json` exists; update it.

## Guardrails

- NEVER copy or commit source PDFs or extracted content outside `Deustch_Books/` and `backend/data/` (both gitignored - copyrighted).
- Rate-limit vision calls to stay inside NIM free tier; if quota is hit, stop and report progress, don't spin.
