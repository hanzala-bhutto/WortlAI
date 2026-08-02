---
name: graph-check
description: Audit the WortlAI lexical graph (SQLite typed-edge tables) for quality - orphan words, uncited LLM-extracted edges, suspicious clusters - and sample edges for human review. Use after /ingest or when curriculum suggestions look wrong.
---

# /graph-check - lexical graph quality audit

**Status: live since #12.** Tables are `words` (nodes), `word_links` (typed edges: `from_word_id`, `to_word_id`, `edge_type`, `source`, `citation`, `detail`), and `error_pattern_links`. If a table is missing (a DB predating the migration), report that and stop.

## Checks (SQL against `backend/data/wortlai.db`)

1. **Orphans**: `words` with no `word_links` (in or out) and no `topic`/`chapter` - count + sample 10.
2. **Citation rule**: LLM edges (`source='llm'`) with NULL/empty `citation`. The `ck_word_links_llm_edge_cited` CHECK makes these unwritable, so a non-zero count means the constraint was bypassed - list for deletion and investigate how they got in.
3. **Degree outliers**: words with >30 edges (likely extraction noise) - sample their edges.
4. **Family sanity**: sample 10 `IN_FAMILY` clusters; flag members not sharing a `verb_infinitive` for human eyes (the derivation groups on it, so a mismatch signals bad data).
5. **Government edges**: every `GOVERNS` edge must carry a well-formed `detail` (preposition+case, e.g. `auf+Akk`); list edges whose `detail` is NULL or not matching `^\w+\+(Akk|Dat|Gen|Nom)$`.
6. **Symmetry**: `SYNONYM`/`ANTONYM`/`IN_FAMILY` edges missing their reverse (the writer adds both directions, so a gap means a hand-edit or partial write).

## Report

Counts per check, worst offenders, and a numbered list of 10 random edges (word → type → word, citation) for Hanzala to eyeball. End with a verdict: graph healthy / needs cleanup, and if cleanup: the exact SQL to fix each issue class - **show the SQL, don't execute deletions without approval**.
