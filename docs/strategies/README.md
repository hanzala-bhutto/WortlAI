# Strategies

How WortlAI actually does a thing **right now**, and what we gave up to do it that way.
The living record behind CLAUDE.md's terse "Stack decisions" one-liners.

If a feasibility report is a frozen decision in the conditional ("we could..."), a strategy doc is
the present-tense truth ("we chunk glossary entries per-lemma, top-k 5, no rerank, because..."). It is
the doc you hand a new contributor, or re-read yourself before touching retrieval in three months.

## When to write or update one (curated - per subsystem, never per issue)

- **One file per subsystem, not per task**: `rag-retrieval.md`, `rag-chunking.md`, `fsrs-grading.md`,
  `voice-pipeline.md`, `lexical-graph.md`. Create a file the first time a subsystem's approach settles.
- **Update in place.** When we change how retrieval works, edit `rag-retrieval.md` - do not mint a v2.
  The file always describes the current system. Git history holds the past.
- Most issues touch an existing strategy doc or none. Minting a new one is rare.

## What every strategy doc covers

1. **Approach** - how we do it, in the present tense.
2. **Parameters** - the actual values we run (chunk size, top-k, model id, thresholds).
3. **Tradeoffs accepted** - what this choice costs us and what we'd switch to if a constraint changed.
4. **Consumers** - which agent / endpoint / skill relies on this. (The "who uses it" axis feasibility skips.)

## Relationship to CLAUDE.md

CLAUDE.md's "Stack decisions (audited, don't re-litigate)" stays the terse law and the index.
Strategy docs are the expanded record behind each line: CLAUDE.md says *"Qdrant kept for the LlamaIndex
integration"*; `rag-retrieval.md` says why, with the collection layout, the top-k, and the exit plan
if the corpus outgrows it. If a strategy doc and CLAUDE.md ever disagree, CLAUDE.md wins - fix the doc.
