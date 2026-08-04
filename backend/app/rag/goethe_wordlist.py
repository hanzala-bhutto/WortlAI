"""Deterministic parser for the Goethe-Institut A1 (Start Deutsch 1) Wortliste (#13).

First increment: the A1 list only. It is single-column with a fixed two-zone
geometry - a headword+grammar zone at the left margin (x0 ~143) and an example
zone at x0 ~237 - and that column boundary is the entire segmentation signal:

  - a row with a token in the headword zone starts a **new entry**; its
    headword-zone text is the lemma + grammar, its example-zone text the first
    line of the example.
  - a row whose tokens all sit in the example zone is a **continuation** - a
    wrapped or additional example sentence - appended to the current entry.

Nothing guesses where the grammar ends and the sentence begins, because the PDF
already separates them by x-position. This is #9's column-split idea (glossary_parser
`_split_columns`) repurposed: there it split German from English, here it splits
headword from example, and a missing left zone marks a continuation.

Emits the shared `WordRecord` (glossary_parser), so `persist_words` and
`derive_family_edges` ingest Goethe rows on the same path as the glossary. The A2/B1
Wortlisten are two-column (docs/experiments/013-goethe-wordlist-extractability.md)
and get their own layout stage behind this same seam in a later increment.

What the A1 list does *not* carry, by design: no English (translation_en stays
None), no verb conjugation (pos is noun via the article, verb only for the "(sich)"
reflexive marker, else "other" - the list gives no signal to tell a bare verb from
an adverb, and precision is preferred over a guess).
"""

import re
from pathlib import Path

import pdfplumber

from app.rag.glossary_parser import WordRecord

# The example column's left edge. Measured at x0 ~236.5 across the A1 SD1 pages;
# headword-zone tokens run x0 143-230, so any split in (230, 236) separates them.
# A row with no token left of this is a continuation, not a new entry.
_EXAMPLE_COL_X0 = 232
_ROW_TOLERANCE = 3  # points of 'top' jitter that still count as one visual row
_WORD_X_TOLERANCE = 2  # clean font (no dropped-vowel bug like the Netzwerk glossary)

# The alphabetical list is bracketed by a heading and the bibliography; parse only
# between them so the Wortgruppen/Inventar front matter and the Literatur back matter
# never become word nodes. Content markers, not page numbers, so the same guard
# survives a re-paginated source. Anchored at line start so the "Alphabetische
# Wortliste" *heading* (a hanging heading, alone on its row) triggers it, but the
# table-of-contents line ("9 Alphabetische Wortliste", starting with a page number)
# does not - that TOC match was flipping the list on early and dragging the intro in.
_LIST_START_RE = re.compile(r"^Alphabetische", re.IGNORECASE)
_LIST_END_RE = re.compile(r"^LITerATur", re.IGNORECASE)

# Per-page furniture that sits in a word zone but is not an entry: the running page
# code (213082_20_SV), the "INVeNTAre" running head, "Seite N" footer.
_BOILERPLATE_RE = re.compile(
    r"^(\d{4,}_\w+|INVeNTAre|WORTLISTE.*|Seite\s+\d+|Alphabetische.*|wortliste)$",
    re.IGNORECASE,
)
# A single letter alone in the headword zone is an A-Z section divider, not a word.
_SECTION_LETTER_RE = re.compile(r"^[A-Za-zÄÖÜäöü]$")

# A noun entry: lowercase article + the noun; everything after the noun (a comma +
# plural marker, or a "(pl.)"/"(Sg.)" note) is the plural spec. The article being
# lowercase is what separates an entry ("die Frau, -en ...") from a sentence-initial
# "Die ..." - but by this point x-position has already isolated the headword zone,
# so the whole string here is grammar, never example text.
_ARTICLE_NOUN_RE = re.compile(r"^(der|die|das)\s+([A-ZÄÖÜ][\wäöüß.\-]*)\s*(.*)$")
# Reflexive verb: the source marks it either "(sich) X" or bare "sich X"; both are
# verbs. A lone "sich" (the pronoun entry) has no following word and stays "other".
_REFLEXIVE_RE = re.compile(r"^(?:\(sich\)|sich)\s+(.+)$")
# An "other" headword that still looks like a noun the article rule missed - a dual
# article ("der/die Bekannte") or a capitalised head carrying a plural marker
# ("Satz, -ä, e") - is flagged for review rather than trusted as a clean node.
_NOUNLIKE_OTHER_RE = re.compile(r"^(?:der|die|das)/|^[A-ZÄÖÜ][\wäöüß.\-]*,\s*[-¨ÄÖÜ]")
# A wrapped plural marker that spilled into the example zone - either alone on the
# headword row ("-en") or leading a continuation row ("-en Welche Sehenswürdigkeiten
# ..."). The lead is a hyphen ("-en") or a bare diaeresis ("¨e" -> umlaut plural), the
# two marker shapes _NOUNLIKE_OTHER_RE also anticipates; without the "¨" a diaeresis-
# only marker would wrap silently into the example text instead of the plural field.
# Group 2 is the example remainder, if any.
_WRAPPED_PLURAL_RE = re.compile(r"^([-¨]\w+)(?:\s+([A-ZÄÖÜ].*))?$")


def _rows_from_page(page: "pdfplumber.page.Page") -> list[list[dict]]:
    """Group a page's words into visual rows, tolerant of a few points of jitter."""
    words = sorted(
        page.extract_words(x_tolerance=_WORD_X_TOLERANCE),
        key=lambda w: (w["top"], w["x0"]),
    )
    rows: list[list[dict]] = []
    for w in words:
        if rows and abs(rows[-1][0]["top"] - w["top"]) <= _ROW_TOLERANCE:
            rows[-1].append(w)
        else:
            rows.append([w])
    for row in rows:
        row.sort(key=lambda w: w["x0"])
    return rows


def _split_zones(row: list[dict]) -> tuple[str, str]:
    """Split a row into (headword-zone text, example-zone text) at the example
    column. An empty headword zone marks a continuation line."""
    head = " ".join(w["text"] for w in row if w["x0"] < _EXAMPLE_COL_X0)
    example = " ".join(w["text"] for w in row if w["x0"] >= _EXAMPLE_COL_X0)
    return head.strip(), example.strip()


def _classify_entry(headword: str, example: str, page_no: int) -> WordRecord:
    """Parse one headword-zone string + its example into a WordRecord.

    Pure and layout-free: the caller has already isolated `headword` (lemma +
    grammar) from `example` by x-position, so this only reads grammar. An unparsable
    headword still stores, flagged `needs_review` with the raw text kept in
    `lemma_raw`, rather than being dropped or guessed (the #9 discipline)."""
    needs_review = False

    noun = _ARTICLE_NOUN_RE.match(headword)
    if noun:
        article, lemma, rest = noun.groups()
        plural = rest.lstrip(",").strip() or None
        # A plural spec ending in "/" is half of an "X, -en / Y, -er" paired entry;
        # the tail (and often the example) went to the next row, so flag it.
        if plural and plural.endswith("/"):
            plural = plural.rstrip("/").strip() or None
            needs_review = True
        return WordRecord(
            lemma=lemma,
            lemma_raw=headword,
            pos="noun",
            article=article,
            plural=plural,
            example_de=example or None,
            level="A1",
            source_page=page_no,
            needs_review=needs_review,
        )

    reflexive = _REFLEXIVE_RE.match(headword)
    if reflexive:
        return WordRecord(
            lemma=reflexive.group(1).strip(),
            lemma_raw=headword,
            pos="verb",
            example_de=example or None,
            level="A1",
            source_page=page_no,
            needs_review=needs_review,
        )

    # Anything else - adjectives, adverbs, prepositions, particles, bare verbs,
    # multiword heads ("an sein") - is "other"; the A1 list gives no signal to
    # classify it further. An empty lemma, or a head that still looks like a noun
    # the article rule missed, is flagged rather than trusted.
    lemma = headword.strip()
    if not lemma or _NOUNLIKE_OTHER_RE.match(lemma):
        needs_review = True
    return WordRecord(
        lemma=lemma,
        lemma_raw=headword,
        pos="other",
        example_de=example or None,
        level="A1",
        source_page=page_no,
        needs_review=needs_review,
    )


def parse_a1_wordlist(pdf_path: Path) -> list[WordRecord]:
    """Parse the Goethe A1 (Start Deutsch 1) Wortliste into WordRecords.

    Only the alphabetical section (between the "Alphabetische Wortliste" heading and
    the "Literatur" bibliography) becomes word nodes. Within it, entries are segmented
    by the headword/example column split, and an entry's example is the join of its
    own example-zone text and every following continuation row."""
    records: list[WordRecord] = []
    in_list = False
    pending: WordRecord | None = None
    example_parts: list[str] = []

    def flush() -> None:
        nonlocal pending, example_parts
        if pending is not None:
            joined = " ".join(p for p in example_parts if p).strip()
            pending.example_de = joined or None
            records.append(pending)
        pending, example_parts = None, []

    def reclaim_plural(entry: WordRecord | None, text: str) -> str:
        """If `text` (from the example zone) leads with a plural marker that wrapped
        out of `entry`'s grammar, move it onto the noun and return the remainder."""
        wrapped = _WRAPPED_PLURAL_RE.match(text)
        if (
            wrapped
            and entry is not None
            and entry.pos == "noun"
            and entry.plural is None
        ):
            entry.plural = wrapped.group(1)
            return wrapped.group(2) or ""
        return text

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_no = page.page_number
            for row in _rows_from_page(page):
                head, example = _split_zones(row)
                line = f"{head} {example}".strip()

                if not in_list:
                    if _LIST_START_RE.search(line):
                        in_list = True
                    continue
                if _LIST_END_RE.match(line):
                    flush()
                    in_list = False
                    continue

                if not head:
                    # continuation: example-only row, belongs to the current entry -
                    # unless it is the running head / page footer, which also lands in
                    # the example zone and would otherwise bleed into the last entry.
                    if example and not _BOILERPLATE_RE.match(example):
                        # a plural marker that wrapped into the example zone belongs to
                        # the current noun, not to its example sentence.
                        example = reclaim_plural(pending, example)
                        if example:
                            example_parts.append(example)
                    continue
                if _BOILERPLATE_RE.match(head) or _SECTION_LETTER_RE.match(head):
                    continue

                # A compound noun whose lemma wrapped at a hyphen: the previous noun
                # ("der Anruf-") and this row's tail ("beantworter") are one word. Merge
                # rather than emit two junk nodes. Bare stems (all-, ander-) never trip
                # this: they have no article, so they are not nouns.
                if (
                    pending is not None
                    and pending.pos == "noun"
                    and pending.lemma.endswith("-")
                ):
                    combined = pending.lemma_raw[:-1] + head
                    if example:
                        example_parts.append(example)
                    pending = _classify_entry(combined, "", pending.source_page)
                    continue

                # a headword-zone token: a new entry starts here
                flush()
                pending = _classify_entry(head, example, page_no)
                # a plural marker can also wrap into this row's own example zone
                # ("die Sehenswürdigkeit," | "-en"), not just a later continuation row.
                example = reclaim_plural(pending, example)
                example_parts = [example] if example else []

    flush()
    return records
