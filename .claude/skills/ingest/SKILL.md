---
name: ingest
description: Run the full WortlAI ingestion pipeline over Deustch_Books/ - parse glossaries, vision-extract textbook pages, rebuild Qdrant collections and the SQLite lexical graph - and report stats. Use after adding/changing source PDFs or ingestion code.
---

# /ingest - rebuild knowledge stores from source PDFs

**Status: partial.** Only the Goethe Wortliste path is wired (#13). The glossary (#9),
vision (#10) and Qdrant (#11) paths are separate issues; the orchestrator
`backend/app/rag/ingest.py` is the seam they slot into, so treat the steps below for
those sources as not-yet-runnable and report that rather than inventing a command.

## Steps

1. Verify Qdrant is up (`curl http://localhost:6333/readyz`); if not, start it (`docker compose up -d`). (Only needed once the Qdrant path is wired; the Goethe path writes SQLite only.)
2. Run the pipeline from `backend/`: `python -m app.rag.ingest --source ../Deutsch_Books`
   - **Goethe wordlists (wired):** `Deutsch_Books/goethe/wortlisten/{A1,A2,B1}/` → deterministic parsers → word nodes stamped `source="goethe"` with an authoritative CEFR `level`; verb-family edges derived. Ambiguous rows are persisted with `needs_review=True`, never silently trusted.
   - Glossary PDFs (NWn_*Glossar*) → deterministic parser → structured word records. *(Not yet wired - #9 parser exists, no ingest caller.)*
   - Kursbuch/Übungsbuch → vision extraction (NIM vision model) → Redemittel chunks + grammar boxes, cached per page hash in `backend/data/vision_cache/`. *(Not yet wired - #10.)*
3. Rebuild (future): Qdrant collections `vocab` + `content`; the rest of the SQLite chunk tables. Deterministic edges from parse; LLM relational edges must carry a citation (example sentence) or are dropped.

## Report

- Words parsed per source (per level for Goethe); flagged `needs_review` counts; later: chunks extracted, Qdrant points per collection, edges by type (deterministic vs LLM-extracted vs dropped-uncited), orphan words.
- Diff vs previous run from the stats file `backend/data/ingest_stats.json`; the Goethe path writes/updates it.

## Guardrails

- NEVER copy or commit source PDFs or extracted content outside `Deustch_Books/` and `backend/data/` (both gitignored - copyrighted).
- Rate-limit vision calls to stay inside NIM free tier; if quota is hit, stop and report progress, don't spin.
