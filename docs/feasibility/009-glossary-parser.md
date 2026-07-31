# Feasibility: 009 - Deterministic glossary parser (Netzwerk Glossar PDFs -> structured word records)

- **Issue**: #9 - **Phase**: 2 - **Date**: 2026-07-31 - **Author**: Claude (reviewed by Hanzala)

## Goal
Parse the Netzwerk neu chapter glossary PDFs into structured, Pydantic-validated word records (article, plural, verb forms, example sentence, English translation, chapter/topic, level) that seed the Qdrant vocab collection and the SQLite lexical graph's structural facts, per docs/PLAN.md's RAG design. Issues #10-#20 consume this record shape, so getting it right now matters more than getting it fast.

## Investigation (PDFs actually opened, not assumed)

Inspected with pdfplumber 0.11 and PyMuPDF (fitz) against the real files in Deutsch_Books/klett/netzwerk_neu/ (gitignored, read locally only). Every page of every candidate file was scanned (not a sample), counting page.chars (real text) vs page.images (raster content):

- A1/glossare/NWn_A1_Glossar_Deutsch-Englisch.pdf (48 pages) - real embedded text on every page, page.chars 1,300-2,100 per page across all 48 pages; a small, roughly constant 15-32 decorative images per page (logos/rules, not glossary content).
- A2/glossare/NWn_A2_Glossar_Englisch1.pdf (37 pages, byte-identical to the copy at Deutsch_Books/kursbuecher/) - checked every one of its 37 pages: all 37 carry only 374-434 real chars (page furniture only: header, footer, exercise-number anchors like 6b, Kapitel N: headings) plus 150-305 raster images per page. This is not a cover-page artifact; it is uniform across the whole document. extract_text() returns almost nothing on any content page.
- B1/glossare/NWn_B1_Glossar_Englisch.pdf (45 pages) - same pattern, all 45 pages: 222-347 images/page, 326-363 real chars/page.

This is the single most important finding and changes #9's scope: the deterministic parser applies to the A1-style text-based glossary. The A2/B1 English glossaries have no extractable glossary text anywhere in the document - not even mostly, not just page 1 - so they need the vision-extraction path already planned in docs/PLAN.md for Kursbuch pages (rag/vision_extract.py), not a regex parser. Recommend scoping #9 to "A1 Deutsch-Englisch (and any other text-based Netzwerk glossary)" and routing A2/B1 English glossaries to the existing vision-extraction track (issue #10 per docs/PLAN.md) rather than blocking #9 on OCR.

### Layout of the text-based (A1) file

Two columns, confirmed via page.extract_words() x0-clustering: German entries left column (x0 ~ 120-127), English translations right column (x0 ~ 321). Left margin (x0 ~ 74) carries either an exercise-number anchor (6b, 7a, 1a UeB) or a bare "Kapitel N: <topic>" heading; unnumbered bold subheadings at the German-column x-position (e.g. "Laender und Sprachen", "kurz und klar") mark topic groups within a chapter. Real extracted rows (page 4, pdfplumber.extract_text()):

    6b die Handynummer, -n mobile phone number
    7a das Alphabet, -e alphabet
    mit|lesen, er liest mit, hat mitgelesen to read along
    7b die E-Mail-Adresse, -n email address
    das Gespraech, -e conversation

and page 5:

    Kapitel 2: Freunde, Kollegen und ich
    2a der Kollege, -n colleague (m)
    singen, er singt, hat gesungen to sing
    gehen, er geht, ist gegangen (Gehst du to go (Do you like going to the cinema?)
    gern ins Kino?)
    das Kino, -s movie theater

One entry per line for simple nouns/adjectives; multi-line for verb conjugations and any entry with a parenthetical usage example (the German-column wrap and the English-column wrap are independent line counts, so entries must be re-joined by y-position band, not by literal newline count).

### The stress-mark bug (second key finding)

Word-stress is typeset by an extra underline glyph fused onto the stressed vowel in a custom font subset (ABCDEE+PoloST11KLeicht/Bold/Buch). Neither library preserves it cleanly - confirmed at char level by comparing page.chars bounding boxes against the rendered word:

- pdfplumber: the stressed-vowel character is genuinely absent from the content stream at that position. Example: "elf" extracts as "lf" - no char object exists between x0=120.86 (word start) and x0=126.74 (where "l" begins). Same for "fuenf" -> "f nf", "Handynummer" -> "H ndynummer" (missing "a"), "mit|sprechen" -> "mt|sprechen" (missing "i").
- PyMuPDF get_text('text'): renders a stray space or a replacement character instead of dropping it outright, e.g. "elf" -> "e lf", "fuenf" -> "f[?] nf".

Every stressed vowel in the corpus is corrupted by naive extraction - this is not an edge case, it happens on essentially every multi-syllable word. Any deterministic parser must include a reconstruction pass; skipping it silently poisons the lemma field for the whole vocab collection.

### Efficiency measured (not estimated)

On the A1 file (48 pages, backend/.venv):
- page.extract_words() (pdfplumber's layout-clustered word extraction, what a naive parser would reach for first): 38.6s / 14,523 words, 181MB peak (tracemalloc).
- page.chars (raw character stream, no clustering - what the reconstruction pass needs anyway): 7.8s / 87,070 chars.

Recommendation: parse from page.chars directly (custom row grouping by rounded "top", column split at x ~ 300) rather than extract_words/extract_text - about 5x faster, and it is the only way to see the stress-mark gaps at all. Either number is fine for a one-off offline ingestion job (docs/PLAN.md's "batch offline job, one-time per book" framing); this is not a runtime-path concern.

## Proposed schema

    class VerbForm(BaseModel):
        prefix: str | None = None          # separable prefix, e.g. "mit" from "mit|sprechen"
        infinitive: str
        third_person_present: str | None = None   # "er spricht mit"
        perfect_auxiliary: Literal["haben", "sein"] | None = None
        past_participle: str | None = None

    class WordRecord(BaseModel):
        lemma: str                          # reconstructed, stress marks stripped for matching
        lemma_raw: str                      # exact extracted string, pre-reconstruction, for audit
        pos: Literal["noun", "verb", "adj", "adv", "prep", "other"]
        article: Literal["der", "die", "das"] | None = None
        plural: str | None = None           # normalized suffix, e.g. "-en", '"-er'
        singular_only: bool = False         # "(Sg.)" marker
        verb: VerbForm | None = None
        example_sentence_de: str | None = None
        example_sentence_en: str | None = None
        translation_en: str
        usage_note: str | None = None       # e.g. "here: about", case governance "(+ D.)"
        chapter: int
        chapter_title: str
        topic: str | None = None            # subheading, e.g. "Laender und Sprachen"
        exercise_ref: str | None = None     # "6b", "7a" - traceability to the source page
        level: Literal["A1", "A2", "B1"]
        source_pdf: str
        source_page: int
        extraction_method: Literal["deterministic", "llm_assisted"] = "deterministic"
        needs_review: bool = False          # set when reconstruction was ambiguous

lemma_raw plus extraction_method plus needs_review exist specifically so a bad reconstruction is inspectable and re-runnable without re-parsing the PDF, and so guardrail 1 (parse-and-validate, never let unvalidated text reach the DB) has something concrete to gate on.

## Parsing strategy

Deterministic, rule-based, no LLM in the primary path:

1. **Row grouping**: cluster page.chars by rounded "top" (+/-2pt tolerance) into visual rows; split each row at x ~ 300 into German/English half-rows.
2. **Entry joining**: an entry starts at a row whose German half begins with an article (der/die/das), a lowercase word, or a capitalized noun, and continues (both halves) until the next row that starts a new entry or hits an exercise-anchor/chapter-heading row. This handles the two-line verb-conjugation and parenthetical-example cases seen above.
3. **Stress-mark reconstruction**: within an entry's German text, detect the pdfplumber gap pattern (adjacent chars whose x-distance exceeds ~1.3x the font's median advance width) or the PyMuPDF stray-space/replacement-char pattern, and fill the gap by fuzzy-matching the corrupted token (edit distance 1, all 8 stress-vowel candidates: a, e, i, o, u, ae, oe, ue) against a canonical German wordlist (start with de_core_news_sm's vocabulary, already a project dependency used downstream for transcript lemmatization in Phase 2). Unresolved corruptions (ambiguous edit-distance-1 matches, or none) get needs_review=True; extraction_method stays "deterministic" - it's flagged, never silently guessed.
4. **Field regexes** on the reconstructed German half:
   - Article + plural: `^(der|die|das)\s+([A-ZÄÖÜ][\wäöüß-]+),\s*(-\S*|"-\S*|\(Pl\.\)|\(Sg\.\))?`
   - Verb: `^([\wäöüß]+)(?:\|([\wäöüß]+))?,\s*er\s+([\wäöüß]+)(?:,\s*(hat|ist)\s+([\wäöüß]+))?` (separable prefix carried by the pipe character).
   - Parenthetical usage/example: `\(([^()]*)\)` extracted greedily, then split into a case-governance marker ("+ D." / "+ A."), a "here:" gloss, or a full example sentence by presence of a verb-like token.
   - Chapter heading: `^Kapitel\s+(\d+):\s*(.+)$`.
5. **LLM-assisted fallback** (guardrail 6-compliant, used sparingly): rows that fail all regexes after reconstruction (expect: idioms, multi-clause examples, the rare three-line entry) go to a small batch prompt that must return the same WordRecord Pydantic shape; the raw PDF row text is passed as delimited **data**, never instruction position, and the LLM output still goes through the same Pydantic validation + retry-once-then-drop path as every other agent output in this codebase (app/agents/ pattern), plus needs_review=True. No LLM call is required to ship #9 - only to raise recall on the long tail of malformed lines within the A1 file itself (not a substitute for the A2/B1 image problem, which is a different pipeline entirely).

## Accuracy measurement plan

1. **Gold set**: hand-label 40 entries from the A1 file, stratified across Kapitel 1-3 (pages already inspected: numbers/greetings, countries/languages, hobbies/friends) to cover nouns, separable verbs, "(Sg.)"/"(Pl.)" markers, and parenthetical examples. Store as `backend/tests/fixtures/glossary_gold/netzwerk_a1_gold.jsonl`, one WordRecord-shaped JSON object per line, plus `source_page`/`exercise_ref` so a human can re-check against the PDF.
2. **Metric**: per-field precision and recall against the gold set - `article`, `plural`, `verb.*`, `example_sentence_de/en`, `translation_en`, `chapter`, `topic`. Precision = correct / attempted-non-null; recall = correct / gold-non-null. Report separately the **stress-mark corruption rate** (corrupted lemmas / total lemmas) and the **post-reconstruction residual error rate** (still wrong after fuzzy-match), since that is the parser's actual novel risk, not a generic PDF-parsing metric.
3. **Where it lives**: `backend/scripts/eval_glossary_parser.py`, following the shape of the `/eval` skill (docs/PLAN.md's LLMOps section: offline harness, metric deltas gate promotion) - run it after any regex/reconstruction change, print a per-field table, and fail CI-visibly if precision/recall on `article`/`plural`/`translation_en` drops below a checked-in baseline (target >=95% given these are the highest-confidence fields; `example_sentence`/`topic` target >=85%, since parenthetical splitting is the fuzziest rule).
4. **Efficiency**: already measured above (7.8s char-extraction / 38.6s if using extract_words, 181MB peak on the 48-page A1 file) - log both in the eval script's output so a future regression (e.g. someone reintroducing extract_words) is visible in the same report as accuracy.

## Prior art

No maintained open-source Netzwerk-glossary-specific parser found (these are copyrighted Klett PDFs; nothing indexed publicly parses them). General dictionary-PDF-to-structured-data tooling (e.g. pdf2dict-style projects, Anki German-deck generators using pdfplumber) confirms the same two techniques used here - column-split by x-coordinate and char-level gap detection for missing-glyph fonts - as the standard approach; nothing off-the-shelf handles this specific stress-mark font quirk, so the reconstruction pass is bespoke regardless of prior art.

## Risks & unknowns

- **Scope risk**: A2/B1 English glossaries need vision extraction, not this parser - confirmed by a full per-page scan (all 37 A2 pages, all 45 B1 pages are image-based, not just a cover page). Re-scope #9's acceptance criteria to text-based glossaries (A1, and any future glossary that passes the same page.chars-per-page check) before implementation starts, and route A2/B1 English glossaries to the existing Kursbuch vision-extraction track (#10) so #10-#20 are not blocked waiting on OCR.
- **Fuzzy-match reconstruction could introduce wrong-but-plausible words** (wrong edit-distance-1 candidate) -> mitigated by the `needs_review` flag plus gold-set residual-error tracking; never write an unreviewed reconstruction to the DB, per guardrail 1/5.
- **Other-language Netzwerk glossaries in the same directories** (Arabic, French, Russian, Greek, etc.) were not inspected - out of scope for #9 (English is the only translation column Hanzala needs) but worth a one-line note in the parser module that non-English variants are untested and may have a different column layout.
- **`de_core_news_sm` as the reconstruction dictionary** may not cover every A1 word (proper nouns, numbers) - fall back to a small curated exception list for the ~20 number/country words seen in the sample rather than over-fitting the fuzzy matcher.

## Free-tier impact
None for the deterministic path (no LLM/API calls). The LLM-assisted fallback for unparseable rows is a handful of batched Groq calls per glossary (expect under 20 rows out of roughly 1,300 A1 entries), negligible against the free-tier budget.

## Effort estimate
M (half-day) for the deterministic parser + reconstruction pass + regex fields; +S (<2h) for the gold set and eval script. LLM-assisted fallback is optional for the first cut of #9 and can land as a fast-follow once the residual-error rate from the eval script justifies it.

## Verdict
**GO** on the A1 Deutsch-Englisch glossary with the char-level extraction + stress-mark reconstruction approach above. **Re-scope, do not block**: the A2/B1 English glossaries are image-based end to end (verified per-page, not just page 1) and belong to the vision-extraction track (#10), not this issue. First implementation steps: (1) add pdfplumber to `backend/requirements.txt` behind `rag/glossary_parser.py`; (2) implement char-row grouping + stress-mark reconstruction against `de_core_news_sm`; (3) hand-label the 40-entry gold set from Kapitel 1-3; (4) wire `eval_glossary_parser.py` and check the baseline numbers in before touching #10.
