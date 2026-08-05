# Feasibility: 013 - Goethe wordlist ingestion for objective level coverage

- **Issue**: #13
- **Phase / Milestone**: Phase 2 (RAG + Learner Model + FSRS)
- **Date**: 2026-08-02
- **Author**: Claude (reviewed by Hanzala)

## Goal
Ingest the official Goethe-Institut A1/A2/B1 Wortlisten into the `words` table so every
lemma carries an authoritative CEFR level from the exam board, not one inferred from a
course book's chapter order. This gives the Curriculum and Assessment agents an objective
"is this word A2 or B1" signal, and lets coverage be measured against a fixed external
target instead of against whatever the Netzwerk glossary happened to include.

## Approach options
1. **New deterministic text parser emitting the shared `WordRecord`, reusing `persist_words` (chosen).**
   The Goethe wordlists are text-layer on every level (experiment 013), so a `pdfplumber`
   parser reads them without vision. It emits the existing `rag/glossary_parser.py::WordRecord`,
   then `persist_words` upserts nodes and `derive_family_edges` builds IN_FAMILY edges, exactly
   as the A1 glossary path does. The schema already anticipates this: `Word.source` lists
   `goethe` and the natural key `(lemma, pos, level)` lets a Goethe row coexist with a glossary
   row of the same lemma at a different level. Reuses the entire write + edge path; only the
   parser is new. Internally the parser splits into two stages so the two layouts (below) share
   one grammar: a **layout stage** yields ordered entry text-blocks per file, and an **entry
   stage** parses a block into a `WordRecord`. Only the layout stage differs per level.
2. **Extend `glossary_parser.py` to also handle the Goethe layout.** Rejected. Its regexes
   encode the Netzwerk two-column German/English layout, chapter/topic/exercise anchors, and
   the dropped-vowel font workaround - none of which apply to the Goethe single-column,
   German-only, alphabetical list. Overloading one parser with two document grammars makes
   both harder to reason about. A separate parser behind the same `WordRecord` seam is cleaner.
3. **Vision-extract the wordlists (the #10 track).** Rejected by experiment 013: the text
   layer is clean on all four PDFs, so vision would add LLM cost and error for no gain.

## Scope decisions (confirmed with Hanzala)
- **A1 source = `A1_SD1_Wortliste_02.pdf`** (Start Deutsch 1, adults). The `Fit1` youth list
  is skipped to avoid near-duplicate A1 nodes; Hanzala is an adult learner.
- **Sections**: parse the main alphabetical list as word nodes; carry a word's `WORTGRUPPEN`
  thematic group into the existing `Word.topic` column when present. **Deferred past the A1
  increment**: A1's WORTGRUPPEN is a separate front-matter block, and mapping it back onto
  individual alphabetical entries is its own task; the A1 parser sets `topic=None` for now,
  and the thematic-group join is picked up with (or after) the two-column A2/B1 stage.
- **Two layout paths, one entry grammar**: A1 is single-column, A2/B1 are two-column
  (experiment 013 follow-up). The layout stage handles that difference; everything downstream
  is shared.
- **No English translations**: the Goethe lists are German-only, so Goethe rows are stored
  for level coverage without a translation (see risk 1).

## Risks & unknowns
- **Central risk: splitting the headword+grammar from the example sentence.** Each entry is
  `<headword + grammar markers> <German example sentence>` on one line (or wrapped), with no
  delimiter between the two. The split is deterministic off the grammar templates (noun:
  `(der|die|das) X[, plural-marker]`; verb A2/B1: `infinitive[, konj1, konj2]`; A1 verb/other:
  a bare headword), taking everything after the recognised template as the example. Ambiguous
  and unmatched lines are flagged `needs_review=True` with the raw text in `lemma_raw` rather
  than guessed, mirroring #9's discipline. This is the main parser effort and the main test
  surface.
- **Two-column extraction for A2/B1.** The layout stage clusters `extract_words()` by x0 into
  left/right columns and re-joins wrapped conjugation rows within a column. Same technique as
  #9's `_split_columns`, but the column boundary is measured per file, not hardcoded.
- **`Word.translation_en` is `NOT NULL`, Goethe lists have no English → make it nullable.**
  A small Alembic migration. The alternative (LLM-translating) is rejected: structural fields
  never come from a model (CLAUDE.md). Existing glossary rows are unaffected.
- **`persist_words` hardcodes `word.source = "glossary"`** → add a `source` parameter defaulting
  to `"glossary"`, pass `"goethe"` from the Goethe path. One-line shared edit, existing callers
  unchanged.
- **`WordRecord.level` is `Literal["A1"]`** → widen to `Literal["A1","A2","B1"]`. The Goethe
  parser sets the level per source file.
- **POS detection differs from #9.** Goethe entries mark nouns by article + plural (`Arzt, Ä-e`)
  and give no `er ...` third-person verb clause, so #9's `_VERB_RE` will not fire. The Goethe
  parser needs its own noun/verb/other classification; verbs land as `pos="verb"` with only the
  infinitive (no family-splitting prefix data), so IN_FAMILY edges from Goethe rows will be
  sparse - acceptable, the glossary path is the family-rich source.
- **Lemma overlap across levels is expected, not a bug.** `gehen` may exist as glossary-A1 and
  goethe-A2; the natural key keeps them distinct. The overlap rate is worth measuring (a second
  experiment) but does not block ingest.
- **B1 is 104 pages** vs ~30 for A1/A2 - larger but still deterministic, no free-tier cost.

## Free-tier impact
None. Parsing and DB writes are fully local; no Groq / Whisper / NIM / Langfuse calls. This is
the deterministic track by design.

## Effort estimate
L (1-2 days), revised up from M once the two-layout reality surfaced. Cost drivers, in order:
the headword/example split grammar + its gold-sample tests, then the two-column layout stage
for A2/B1. The DB path is reused, and the `translation_en` migration and two shared-signature
edits are small.

## Verdict
**GO** - the one real unknown (raster vs text) is resolved GO by experiment 013. Remaining work
is a new parser behind an existing seam plus three small, contained schema/signature edits.
