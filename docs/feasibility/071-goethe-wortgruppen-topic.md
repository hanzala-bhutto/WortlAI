# Feasibility: 071 - Goethe WORTGRUPPEN thematic sections into `Word.topic`

- **Issue**: #71
- **Phase / Milestone**: Phase 2 (RAG + Learner Model + FSRS)
- **Date**: 2026-08-06
- **Author**: Claude (reviewed by Hanzala)

## Goal
Parse the thematic **WORTGRUPPEN** material in the Goethe A2/B1 Wortlisten and populate
`Word.topic` - the topic side of the lexical graph - so each word carries an exam-board
topic grouping (`TIERE`, `Uhrzeit`, ...), not only a CEFR level. Split out of #13, which
ingested the alphabetical lists and deliberately left the thematic material for this
increment (`docs/feasibility/013-goethe-wordlist.md`, scope note).

## What the code already gives us (no schema work)
- `WordRecord.topic` and `Word.topic` (indexed, migration `15e0b1d86ddd`) already exist; the
  Qdrant payload already carries `topic`. The alphabetical parser simply leaves `topic=None`.
- `persist_words(db, records, source="goethe")` already copies `topic` onto the row.
- The write path, `WordRecord` seam, and ingest command (`app/rag/ingest.py`) are all reusable.

So the only new code is: a **thematic layout reader** and a **topic-merge write path**.

## Census (fresh, this issue) - corrects experiment 013's page ranges

Probed with `pdfplumber` (`extract_words(x_tolerance=2)`) over the real PDFs.

**B1** (`Goethe-Zertifikat_B1_Wortliste.pdf`, 104 pp):
- **WORTGRUPPEN thematic section = PDF pages 8-15** (topics `1.1`-`1.14`). The
  `2 Alphabetischer Wortschatz` heading starts at **page 16**, and every page from 16 on
  carries example sentences and verb conjugations.
- **Correction:** experiment `013-goethe-a2-b1-layout-census.md` says "thematic pages ~9-39,
  alphabetical 40-103". That is wrong - it conflated the printed page numbers in the TOC
  (`2 Alphabetischer Wortschatz 16`) with PDF indices. The thematic block is ~8 pages, not ~31.
  The census's structural claim (3-column-ish grid, topic headers like `1.11 TIERE`, no example
  sentences) is directionally right but understates how heterogeneous the groups are.

**A2** (`Goethe-Zertifikat_A2_Wortliste.pdf`, 32 pp):
- **Thematic front-matter tables = PDF pages 5-7**, laid out as 2-3 topic **columns** per page,
  each a header + a vertical entry list. Confirmed groups: `Abkürzungen`, `Anweisungssprache
  zur Prüfung`, `Berufe`, `Länder und Nationalitäten`, `Schule und Schulfächer`, `Währungen
  und Maße`, `Uhrzeit`, `Zahlen`. The current A2 parser already excludes these pages
  (`test_a2_front_and_back_matter_excluded`).

### Two structural gotchas (dry-run over the real pages)
1. **Two printed columns are concatenated per extracted text line.** A line like
   `das Abo, -s = das Abonnement, -s/-e der ICE = der Inter City Express` is *left col* +
   *right col*. A line-based reader mixes columns and produces garbage; the reader **must
   split by x0** as `_a2_column_streams` does.
2. **A2's thematic nouns carry no article** (`Arzt, Ä-e / Ärztin, -nen`), so `_ARTICLE_NOUN_RE`
   never fires on A2; A2 needs an article-less `Noun, plural` detector distinct from B1's.

### Per-group census (B1 pp 8-15); noun counts inflated by the column bleed but directional
| Group | Verdict | Shape |
|---|---|---|
| `1.11 TIERE` | keep (pristine) | `der Affe, -n` … 24 clean nouns, 0 slash |
| `1.10 POLITISCHE BEGRIFFE` | keep | nouns, 0 slash |
| `1.8 HIMMELSRICHTUNGEN` | keep (tiny) | `der Norden` … |
| `1.4 BILDUNGSEINRICHTUNGEN` | keep (flag) | nouns, regional, slash-compound cells |
| `1.9 LÄNDER, KONTINENTE, NATIONALITÄTEN` | keep (flag) | nouns + adjectives, regional |
| `1.1 ABKÜRZUNGEN`, `1.2 ANGLIZISMEN` | skip | definitions (`Abo = Abonnement`), mixed noun/verb |
| `1.3 ANWEISUNGSSPRACHE` | skip | full exam-instruction sentences |
| `1.5 SCHULFÄCHER`, `1.7 FARBEN` | skip | article-less subjects / adjectives |
| `1.6 SCHULNOTEN`, `1.12 WÄHRUNGEN`, `1.13 ZAHLEN`, `1.14 ZEIT` | skip | reference data (grades, equations, numbers, dates) |

Hard sub-cases inside the kept groups: cells hold **multiple lemmas joined by `/`**, e.g.
`die Krippe/der Kindergarten/die Kindertagesstätte (Kita)` and
`die Grundschule/Mittelschule/Realschule/Gesamtschule/Berufsschule/Sonderschule` (one article,
a slash-list of nouns). Reliable decomposition into individual `(lemma, article, plural)` is
the real parser risk.

## Scope decisions (confirmed with Hanzala, from the dry-run)
- **Curated noun whitelist, not every group.** Parse only the clean noun groups:
  B1 `TIERE`, `POLITISCHE BEGRIFFE`, `HIMMELSRICHTUNGEN`, `BILDUNGSEINRICHTUNGEN`, `LÄNDER…`,
  plus A2 `Berufe` and `Schule und Schulfächer`. **Skip the reference-data groups entirely** -
  their "lemmas" (`3.15`, `1 € = 1 Euro`, `zweitausendvier`) would be junk word-nodes.
- **Orphan-insert only within the whitelist.** A whitelisted-group noun absent from the
  alphabetical list is real vocabulary → insert as a Goethe row. Because reference-data groups
  are skipped, no numbers/dates/equations ever become nodes.
- The whitelist is a small named constant; widening it later (non-noun groups, more topics) is
  an additive follow-up, not a rewrite.

## Approach options
1. **Deterministic thematic reader emitting `WordRecord`, plus a topic-merge write path (chosen).**
   A new layout stage in `app/rag/goethe_wordlist.py` reads the thematic block (bounded *below*
   the `2 Alphabetischer Wortschatz` anchor the alphabetical parser already keys on, so the two
   stages partition the file cleanly), tracks the current topic header (`^\d+\.\d+\s+[A-ZÄÖÜ]`
   for B1; the column header for A2), and emits `WordRecord`s with `topic` set, `example_de=None`.
   It reuses `_ARTICLE_NOUN_RE` and `_classify_entry`. A new `apply_topics(db, records)` in
   `lexical_graph.py` stamps `topic` onto matching `(lemma, pos, level)` rows **without**
   overwriting other fields, and inserts orphans as new Goethe rows. **Discipline:** the messy
   cells (slash-compounds, non-noun groups) are flagged `needs_review=True` with raw text in
   `lemma_raw`, not guessed - same as #9/#13. Deliver topic coverage on the tractable majority.
2. **Route the thematic stage through `persist_words`.** Rejected. `persist_words` does a
   full-row overwrite on `(lemma, pos, level)` (`lexical_graph.py:119`), so a topic record
   sharing a key with an alphabetical row would null its `example_de`/`verb`/`plural`. Would
   require weakening `persist_words`' overwrite contract for every caller.
3. **Fully decompose every slash-compound and non-noun group.** Rejected for this increment -
   a bespoke parser per table shape is high-effort, high-error, and low-value; flagging beats
   guessing structural fields (CLAUDE.md guardrail 5).

## Central design decision (confirmed with Hanzala)
- **Merge, don't clobber.** Separate `apply_topics` write path; run it *after* the alphabetical
  persist inside `ingest_goethe`, before `derive_family_edges`. Idempotent because ordering is
  fixed in code (alphabetical resets `topic=None`, then the topic pass re-stamps it).
- **Orphan lemmas** within a whitelisted noun group (real vocabulary absent from the
  alphabetical list): **insert as new Goethe rows** (`source="goethe"`, `example_de=None`,
  `topic` set); report the match-vs-insert split as a stat. Reference-data groups are skipped,
  so no junk orphans.

## Risks & unknowns
- **Slash-compound cells** are the main parse risk. Plan: split on `/`, re-attach the leading
  article where present, flag anything ambiguous. Expect a non-trivial `needs_review` rate on
  B1 thematic; that is the main test surface.
- **Non-noun groups** (ABKÜRZUNGEN, FARBEN, ANWEISUNGSSPRACHE) do not fit `_classify_entry`'s
  noun grammar. They land as `pos="other"`/`verb` or `needs_review` - acceptable; topic coverage
  matters more than perfect grammar for reference data.
- **A2 vs B1 layout differ** (A2 = header-topped columns on pp 5-7; B1 = numbered topic blocks on
  pp 8-15). Two small layout readers behind one entry/emit contract, mirroring the A1-vs-A2/B1
  split already in the file.
- **Topic-string normalization:** store the raw header (`TIERE`, `LÄNDER, KONTINENTE, ...`) or a
  slug? Recommend the cleaned human label, trimmed of the `1.11 ` prefix. Low risk, decide in impl.

## Free-tier impact
None. Parsing + DB writes are fully local; no Groq/Whisper/NIM/Langfuse calls.

## Effort estimate
**M (trimmed by the whitelist).** Smaller than #13 (thematic block is ~8 B1 pages + 3 A2 pages,
no verb-conjugation accumulator, no new schema, and only ~7 topic groups parsed). Cost drivers:
the x0 column split + slash-compound splitter and their gold-sample tests, and the article-less
A2 noun detector. Write path and ingest wiring are minor.

## Verdict
**GO.** No schema work, an existing write seam, a bounded and now-correctly-measured source
region. The one real difficulty (heterogeneous cells) is contained by the flag-don't-guess
discipline: parse the clean noun groups for real topic coverage, quarantine the rest as
`needs_review`. Also fold the census page-range correction back into
`docs/experiments/013-goethe-a2-b1-layout-census.md`.
