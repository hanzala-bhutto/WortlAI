# Experiment: 013 - Goethe A2/B1 Wortliste layout census

- **Issue**: #13
- **Date**: 2026-08-04
- **Author**: Claude (reviewed by Hanzala)

## Question
The extractability experiment established A2/B1 are two-column, not single like A1.
Before building the two-column stage: do A2 and B1 actually share *one* layout (as
the feasibility assumed), and what entry grammar does the headword zone carry beyond
A1's `article Noun, plural`? A wrong assumption here is a rewrite, not a tweak.

## Setup
- `pdfplumber`, backend `.venv`, `extract_words(x_tolerance=2)`.
- Probed x0 clusters and row-by-row headword/example content across spread pages of
  `Goethe-Zertifikat_A2_Wortliste.pdf` (32 pp) and `Goethe-Zertifikat_B1_Wortliste.pdf`
  (104 pp): front matter, mid-list, and back matter.

## Results

**A2 - one 2-column alphabetical list.** Front matter (Vorwort, a small thematic
reference table) then the alphabetical `WORTLISTE` on pages ~9-30. Two columns split
at x0 ~290; example prose bridges any inter-column gap, so the split is a fixed
midpoint, not a detected whitespace gap. Within each column the A1 two-zone geometry
repeats (headword zone, then example zone ~66pt right of the column origin). The
headword zone carries a new entry type A1 never had - **verbs with wrapping
conjugation**:

| Source rows (one column) | Parse |
|--------------------------|-------|
| `drucken, druckt,` / `hat gedruckt` | inf=drucken, 3sg=druckt, perf=hat gedruckt |
| `einladen,` / `lädt ein,` / `hat eingeladen` | separable: inf, 3sg=lädt ein, perf |
| `dürfen, darf,` / `durfte,` / `hat gedurft` | modal: inf, 3sg, pret, perf |
| `duschen (sich),` / `duscht,` / `hat geduscht` | reflexive verb |

The form list is comma-terminated and wraps 1-3 continuation rows in the headword
zone, ending on an auxiliary-led perfect (`hat`/`ist ...`). Nouns (`der Drucker, -`)
and bare "other" heads (`dumm`, `durch`, `eigen-`) are unchanged from A1.

**B1 - two different sections, not one.** B1 is *not* a single two-column list:
- **Thematic section** (PDF pages 8-15): a grid grouped under topic headers
  (`1.11 TIERE`), **no example sentences**. This is the WORTGRUPPEN/topic material.
  (Corrected in #71: this census first read "pages ~9-39", conflating the TOC's printed
  page numbers with PDF indices. The `2 Alphabetischer Wortschatz` heading is at PDF page
  16, so the thematic block is ~8 pages, not ~31. The `article Noun, plural` "3-column"
  shape holds only for some groups; others are 1-column or regionally grouped tables -
  see `docs/feasibility/071-goethe-wortgruppen-topic.md`.)
- **Alphabetical section** (PDF pages 16-103): **2-column**, and structurally identical
  to A2's alphabetical list - same verb-conjugation grammar, same midpoint split.

## Finding
The shared layout is the **2-column alphabetical list with verb-conjugation wrapping**,
used by A2 (its whole list) and B1 (its alphabetical section only). A2 and B1 do *not*
share one layout end to end: B1 carries an extra 3-column thematic section up front.
Two consequences for scope:
1. The two-column stage needs a **verb-conjugation accumulator** A1 never required -
   the real new grammar of this increment, and its main test surface.
2. B1's thematic section is the deferred WORTGRUPPEN/topic work
   (`docs/feasibility/013-goethe-wordlist.md`, scope note). B1 alphabetical is parsed
   with the shared stage; B1 thematic is left for the topic increment.

## Fed into
The A2 parser (`app/rag/goethe_wordlist.py`, `parse_a2_wordlist`) and its gold-sample
tests. Sets the boundary for B1: reuse the A2 two-column stage over B1's alphabetical
pages, defer B1's thematic grid. Refines the feasibility's single-layout assumption.
