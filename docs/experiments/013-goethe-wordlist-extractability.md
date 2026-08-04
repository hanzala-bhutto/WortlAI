# Experiment: 013 - Goethe wordlist text-layer extractability

- **Issue**: #13
- **Date**: 2026-08-02
- **Author**: Claude (reviewed by Hanzala)

## Question
Do the official Goethe-Institut A1/A2/B1 Wortlisten carry an extractable text layer,
or are they raster images like the Netzwerk neu A2/B1 glossaries (which forced the
vision-extraction track in #10)? This decides whether #13 is a deterministic text
parser or a vision job.

## Setup
- Corpus: `Deutsch_Books/goethe/wortlisten/{A1,A2,B1}/*.pdf` (4 PDFs).
- Tool: `pdfplumber` (already a dependency), backend `.venv`.
- Method: open each PDF, extract text from a mid-content page (index 4, past the
  cover/intro), count extracted characters and embedded images on that page.
- Fixed: same extraction call for every file. Varied: the file.

## Results

| File | Level | Pages | Mid-page chars | Images on page |
|------|-------|-------|----------------|----------------|
| A1_SD1_Wortliste_02.pdf | A1 | 29 | 2513 | 0 |
| Goethe-Zertifikat_A1_Fit1_Wortliste.pdf | A1 | 28 | 1015 | 1 |
| Goethe-Zertifikat_A2_Wortliste.pdf | A2 | 32 | 1580 | 0 |
| Goethe-Zertifikat_B1_Wortliste.pdf | B1 | 104 | 2153 | 0 |

Extracted text is clean German (umlauts intact in the PDF; the `?` glyphs in raw
console output were a terminal-encoding artefact, not an extraction fault). No sign
of the Netzwerk font's dropped-vowel bug. Structure visible in the text: a thematic
`WORTGRUPPEN` block followed by the main alphabetical list.

## Finding
All four wordlists are text-layer, not raster. `pdfplumber` recovers full entry text
on every level, so **#13 needs no vision track** - one deterministic text parser
covers A1/A2/B1. This is the opposite of the Netzwerk A2/B1 glossaries and removes
the largest risk the issue could have carried.

## Follow-up finding: column layout (same probe session)
An x0 histogram per page shows the levels do **not** share one layout:
- **A1 (`A1_SD1`)**: single column, entries span the full text width; `extract_text()`
  line order is already entry order.
- **A2 and B1**: two columns with a clear x0 gap (left entries ~x0 50-250, right ~300-500).
  `extract_text()` interleaves them into nonsense lines; they need x-position column
  separation (the technique #9 used on the Netzwerk two-column glossary) plus multi-line
  joining, since verb conjugations wrap across rows (`halten, hält,` -> `hat gehalten`).

## Fed into
`docs/feasibility/013-goethe-wordlist.md`: deterministic parser for all three levels, no
`#10` vision dependency, but two layout paths (single- vs two-column) not one, which raises
the effort estimate. Strategy record: `docs/strategies/lexical-graph.md`.
