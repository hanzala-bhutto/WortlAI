# Experiment: 013 - Goethe A1 parser full-list accuracy audit

- **Issue**: #13
- **Date**: 2026-08-02
- **Author**: Claude (reviewed by Hanzala)

## Question
Beyond the page-15 gold sample, how accurate is the A1 Wortliste parser over the
*whole* list, and what error classes remain? A 13-entry gold sample proves the common
path; it does not surface rare layout accidents spread across 700 entries.

## Setup
- Parsed the full Goethe A1 (Start Deutsch 1) Wortliste and dumped all records
  (lemma, pos, article, plural, example).
- Method: manual line-by-line read of every record against German ground truth -
  every article checked for correct gender, every plural marker checked, POS and
  example segmentation eyeballed. Not a sampled metric: a full census.

## Results
Pre-fix parse: **688 records**. The article and plural extraction were correct
throughout (they are copied from the source, never guessed): spot-checks like
`der Baum, -ä, e` -> Bäume, `das Buch, -ü, er` -> Bücher, `der Bus, -se` -> Busse all
held. Every defect fell into one class - **entries that wrapped across two rows**:

| Defect | Count | Example | Fix |
|--------|-------|---------|-----|
| Compound noun split at a hyphen | 2 | `der Anruf-` + `beantworter` -> `der Anrufbeantworter` | merge a noun lemma ending in `-` with the next row |
| Plural marker wrapped into example zone | 1 | `die Sehenswürdigkeit,` \| `-en Welche...` | reclaim a leading `-en` marker onto the noun |
| Paired "X, -en / Y, -er" entry (lost example, `/`-dirty plural) | 3 | `die Ehefrau, -en/` | strip `/`, flag `needs_review` |
| Article-less noun-like head -> `other` | 2 | `Satz, -ä, e`; `der/die Bekannte, -n` | flag `needs_review` |
| Bare `sich X` reflexive not tagged verb | 1 | `sich kümmern` | accept `sich` as well as `(sich)` |

~9 imperfect of 688 (**~98.7% clean**), zero from bad gender/plural guessing. The
capital-headword cases feared during design (`Sie`, `Grad`, `Achtung`) all parsed
correctly.

Post-fix parse: **686 records** (two hyphen-compounds merged), **5 flagged
`needs_review`** (the genuinely ambiguous paired/article-less rows), examples clean,
`sich kümmern` now a verb. Effective clean rate **~99.3%**, with the residual honestly
flagged rather than silently wrong. Each fix has a regression test
(`tests/test_goethe_wordlist.py`).

## Finding
The x-position segmentation is sound; every real error came from two-row wrapping, a
small closed set that deterministic rules (hyphen-merge, plural-reclaim) or an honest
`needs_review` flag resolve. No LLM or fuzzy guessing was needed to reach ~99% on the
full list.

## Fed into
The parser fixes in `app/rag/goethe_wordlist.py` (#13). Strategy record to follow in
`docs/strategies/lexical-graph.md`. Establishes the accuracy bar the A2/B1 two-column
increment must clear.
