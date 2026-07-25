---
name: graph-check
description: Audit the WortlAI lexical graph (SQLite typed-edge tables) for quality - orphan words, uncited LLM-extracted edges, suspicious clusters - and sample edges for human review. Use after /ingest or when curriculum suggestions look wrong.
---

# /graph-check - lexical graph quality audit

**Status: graph tables land in Phase 2.** Until `word_links` exists in the SQLite DB, report that and stop.

## Checks (SQL against `backend/data/wortlai.db`)

1. **Orphans**: words with no edges and no topic/chapter - count + sample 10.
2. **Citation rule**: LLM-extracted edges (`source='llm'`) with NULL/empty `citation` - these violate the anti-hallucination rule and must be listed for deletion.
3. **Degree outliers**: words with >30 edges (likely extraction noise) - sample their edges.
4. **Family sanity**: sample 10 `IN_FAMILY` clusters; flag members not sharing a stem/prefix pattern for human eyes.
5. **Government edges**: every `GOVERNS` edge must encode preposition+case (e.g. `auf+Akk`); list malformed ones.
6. **Symmetry**: `SYNONYM`/`ANTONYM` edges missing their reverse edge.

## Report

Counts per check, worst offenders, and a numbered list of 10 random edges (word → type → word, citation) for Hanzala to eyeball. End with a verdict: graph healthy / needs cleanup, and if cleanup: the exact SQL to fix each issue class - **show the SQL, don't execute deletions without approval**.
