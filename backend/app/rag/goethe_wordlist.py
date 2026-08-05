"""Deterministic parser for the Goethe-Institut Wortlisten (#13).

Two layout stages behind one shared entry grammar, all emitting the shared
`WordRecord` (glossary_parser) so `persist_words` and `derive_family_edges` ingest
Goethe rows on the same path as the glossary:

  - `parse_a1_wordlist` - the A1 (Start Deutsch 1) list. Single-column with a fixed
    two-zone geometry: a headword+grammar zone at the left margin (x0 ~143) and an
    example zone at x0 ~237. That column boundary is the whole segmentation signal - a
    token in the headword zone starts a new entry, a row entirely in the example zone
    continues the current one. The A1 list carries no verb conjugation, so a verb is
    only the "(sich)" reflexive marker, else "other".
  - `parse_a2_wordlist` - the A2 list whole, and the B1 alphabetical section. Two
    columns split at a fixed midpoint, each column repeating the A1 two-zone geometry
    (docs/experiments/013-goethe-a2-b1-layout-census.md). Here the headword zone also
    carries verbs with wrapping conjugation ("einladen, lädt ein, hat eingeladen"),
    parsed into a VerbForm. B1's leading thematic section is the deferred WORTGRUPPEN
    material and is not parsed here.

Nothing guesses where the grammar ends and the sentence begins, because the PDF
already separates them by x-position - #9's column-split idea (glossary_parser
`_split_columns`) repurposed. Goethe lists carry no English, so translation_en stays
None. Genuinely ambiguous rows (paired "X / Y" entries, predicate idioms, dual-gender
nouns) are flagged needs_review rather than guessed - the #9 discipline.
"""

import re
from pathlib import Path

import pdfplumber

from app.rag.glossary_parser import VerbForm, WordRecord

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
    r"^(\d{4,}_\w+|INVeNTAre|\d*\s*WORTLISTE.*|ZERTIFIKAT.*|GOETHE-ZERTIFIKAT.*"
    r"|WORTGRUPPEN|\d*\s*ALPHABETISCHER.*|Seite\s+\d+|Alphabetische.*|wortliste)$",
    re.IGNORECASE,
)
# The mirrored running page code (e.g. "625050_40_etsiltroW_2A") is caught by the
# boilerplate filter when it sits alone, but on a few pages it shares a visual row with
# the bottom entry ("625050_40_etsiltroW_2A der Körper, -"); strip the token in place so
# the real entry survives.
_PAGECODE_RE = re.compile(r"\b\d{4,}_\d+_\w+\b")
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

# --- A2/B1 two-column alphabetical layout ---------------------------------------
# The A2 and B1 alphabetical lists print two columns split at a fixed midpoint; example
# prose bridges any inter-column gap, so the split is a measured x-coordinate, not a
# detected whitespace gap (docs/experiments/013-goethe-a2-b1-layout-census.md). Within
# each column the A1 two-zone geometry repeats: a headword zone at the column origin and
# an example zone ~66pt to its right. Measured on the A2 Wortliste.
_A2_COL_MID = 290
_A2_LEFT_EX = 100
_A2_RIGHT_EX = 372
_A2_PAGE_HEADER = "WORTLISTE"

# A verb entry's headword is a comma-separated conjugation (infinitive, 3rd-person,
# [preterite,] perfect) wrapping up to three headword-zone rows. The start row is a
# lowercase infinitive (optionally "(sich)") then a comma; nouns are caught earlier by
# their article, so a leading-lowercase-then-comma head here is a verb, not a noun.
_A2_VERB_START_RE = re.compile(r"^[a-zäöüß]+(?:\s*\(sich\))?,")
# The perfect form is a present-tense auxiliary (haben/sein) plus a participle:
# "hat gedruckt", "ist abgebogen". Requiring the following participle distinguishes it
# from a bare 3rd-person "hat" (haben's own present form), so the modal/auxiliary verbs
# parse correctly. Its arrival closes the conjugation. A2 prints it on its own row; B1
# shares a row with the preterite ("bog ab, ist abgebogen"), so it is found per comma-
# piece, not only at the row start.
_PERFECT_RE = re.compile(r"^(hat|ist|haben|sind)\s+\S")
# A leading auxiliary or preterite marks a stray conjugation fragment for the "other"
# review flag (war/hatte/wird are preterite, never a clean headword).
_AUX_RE = re.compile(r"^(hat|ist|sind|haben|war|hatte|wird)\b")


def _has_perfect(text: str) -> bool:
    """True if any comma-separated form in `text` is a perfect (auxiliary + participle)."""
    return any(_PERFECT_RE.match(p.strip()) for p in text.split(","))


# The alphabetical section opens with an "Alphabetischer Wortschatz" heading (A2 caps
# it, B1 title-cases it), after the WORTGRUPPEN thematic tables. The same phrase also
# appears in B1's table of contents, but there it is indented (x0 ~171) with a right-
# aligned page number, whereas the heading sits at the left margin (x0 < _A2_LEFT_EX).
# The margin position separates the heading (trigger) from the TOC line - the A1
# TOC-vs-heading fix again.
_A2_LIST_START = "alphabetischer"


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


def _classify_entry(
    headword: str, example: str, page_no: int, level: str = "A1"
) -> WordRecord:
    """Parse one headword-zone string + its example into a WordRecord.

    Pure and layout-free: the caller has already isolated `headword` (lemma +
    grammar) from `example` by x-position, so this only reads grammar. An unparsable
    headword still stores, flagged `needs_review` with the raw text kept in
    `lemma_raw`, rather than being dropped or guessed (the #9 discipline). `level`
    stamps the CEFR level of the source list (A1 for the single-column list, A2/B1
    for the two-column lists that reuse this noun/other grammar)."""
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
            level=level,
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
            level=level,
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
        level=level,
        source_page=page_no,
        needs_review=needs_review,
    )


def _reclaim_plural(entry: WordRecord | None, text: str) -> str:
    """If `text` (from the example zone) leads with a plural marker that wrapped out
    of `entry`'s grammar, move it onto the noun and return the remainder. Shared by
    the A1 single-column and A2/B1 two-column stages - a noun's plural can wrap the
    same way in either layout."""
    wrapped = _WRAPPED_PLURAL_RE.match(text)
    if wrapped and entry is not None and entry.pos == "noun" and entry.plural is None:
        entry.plural = wrapped.group(1)
        return wrapped.group(2) or ""
    return text


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
                        example = _reclaim_plural(pending, example)
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
                example = _reclaim_plural(pending, example)
                example_parts = [example] if example else []

    flush()
    return records


# --- A2/B1 two-column stage -----------------------------------------------------


def _a2_column_streams(rows: list[list[dict]]) -> tuple[list, list]:
    """Split a page's rows into left and right column streams, each a list of
    (headword-zone text, example-zone text) in top-to-bottom order. Entries flow down
    one column then the other, so the two streams are parsed independently."""
    left, right = [], []
    for row in rows:
        lh = " ".join(w["text"] for w in row if w["x0"] < _A2_LEFT_EX)
        le = " ".join(w["text"] for w in row if _A2_LEFT_EX <= w["x0"] < _A2_COL_MID)
        rh = " ".join(w["text"] for w in row if _A2_COL_MID <= w["x0"] < _A2_RIGHT_EX)
        rex = " ".join(w["text"] for w in row if w["x0"] >= _A2_RIGHT_EX)
        left.append(
            (_PAGECODE_RE.sub("", lh).strip(), _PAGECODE_RE.sub("", le).strip())
        )
        right.append(
            (_PAGECODE_RE.sub("", rh).strip(), _PAGECODE_RE.sub("", rex).strip())
        )
    return left, right


def _parse_verb_forms(text: str) -> VerbForm:
    """Parse an accumulated verb headword ("einladen, lädt ein, hat eingeladen") into a
    VerbForm. Forms are comma-separated: infinitive first (a reflexive "(sich)" is
    stripped), the auxiliary-led piece is the perfect, the first non-perfect piece after
    the infinitive is the 3rd-person present, and a separable prefix is read off a
    two-word 3rd-person form ("lädt ein" -> "ein"). A preterite piece, when present, is
    not stored - the learner model has no field for it."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return VerbForm(infinitive="")
    infinitive = parts[0].replace("(sich)", "").strip()
    third = perfect_aux = participle = prefix = None
    for piece in parts[1:]:
        m = _PERFECT_RE.match(piece)
        if m and perfect_aux is None:
            perfect_aux = "haben" if m.group(1) in ("hat", "haben") else "sein"
            participle = piece[len(m.group(1)) :].strip() or None
        elif third is None and not _PERFECT_RE.match(piece):
            # the first non-perfect form after the infinitive is the 3rd-person present;
            # a later preterite piece ("durfte", "bog ab") has no field and is dropped.
            third = piece
    if third and " " in third:
        prefix = third.split()[-1]
    return VerbForm(
        prefix=prefix,
        infinitive=infinitive,
        third_person_present=third,
        perfect_auxiliary=perfect_aux,
        past_participle=participle,
    )


def _is_verb_form_continuation(head: str) -> bool:
    """True if `head` extends an open verb's conjugation rather than starting a new
    entry: an article marks a new noun, and a bare word (no comma, no perfect) is a new
    "other"; a wrapped form carries a comma ("durfte,", "lädt ein,", "bog ab, ist
    abgebogen") or is a perfect ("hat gedruckt")."""
    if _ARTICLE_NOUN_RE.match(head):
        return False
    return "," in head or _has_perfect(head)


def _classify_a2_entry(
    head: str, example: str, page_no: int, level: str
) -> tuple[WordRecord, bool]:
    """Classify the first row of a two-column entry, returning (record, verb_open). A
    verb start yields a provisional record whose lemma and VerbForm are filled at flush
    from the accumulated conjugation; nouns and "other" reuse the A1 grammar."""
    if not _ARTICLE_NOUN_RE.match(head) and _A2_VERB_START_RE.match(head):
        return (
            WordRecord(
                lemma="",
                lemma_raw=head,
                pos="verb",
                example_de=example or None,
                level=level,
                source_page=page_no,
            ),
            True,
        )
    return _classify_entry(head, example, page_no, level), False


def _consume_a2_column(
    stream: list, records: list[WordRecord], page_no: int, level: str
) -> None:
    """Fold one column's (head, example) stream into WordRecords, joining verb
    conjugation and example continuation rows onto the current entry. Entries do not
    span columns, so the stream is self-contained and flushed at its end."""
    pending: WordRecord | None = None
    head_parts: list[str] = []
    example_parts: list[str] = []
    verb_open = False
    swallow = False

    def flush() -> None:
        nonlocal pending, head_parts, example_parts, verb_open, swallow
        if pending is not None:
            if pending.pos == "verb":
                pending.verb = _parse_verb_forms(" ".join(head_parts))
                pending.lemma = pending.verb.infinitive
                pending.lemma_raw = " ".join(head_parts)
                # A clean verb lemma is a single infinitive; a space or paren in it means
                # a preposition annotation ("informieren (über)") or a multiword idiom
                # leaked into the infinitive - flag rather than store a malformed lemma.
                if not pending.lemma or " " in pending.lemma or "(" in pending.lemma:
                    pending.needs_review = True
            # An "other" lemma carrying a comma is not a clean word but a fragment of a
            # wrapped idiom or a noun the article rule missed ("die (E-)Mail, -s", "war
            # einverstanden,"): flag it rather than trust it, as A1 flags its ambiguous
            # rows. Genuine "other" heads (dumm, eigen-, an sein) carry no comma.
            if pending.pos == "other" and (
                any(c in pending.lemma for c in ",/(") or _AUX_RE.match(pending.lemma)
            ):
                pending.needs_review = True
            pending.example_de = " ".join(p for p in example_parts if p).strip() or None
            records.append(pending)
        pending, head_parts, example_parts = None, [], []
        verb_open, swallow = False, False

    for head, example in stream:
        if not head:
            if example and not _BOILERPLATE_RE.match(example):
                example = _reclaim_plural(pending, example)
                if example:
                    example_parts.append(example)
            continue
        if _BOILERPLATE_RE.match(head) or _SECTION_LETTER_RE.match(head):
            continue
        if (
            verb_open
            and pending is not None
            and pending.pos == "verb"
            and _is_verb_form_continuation(head)
        ):
            head_parts.append(head)
            if example:
                example_parts.append(example)
            if _has_perfect(head):
                verb_open = False
            continue
        # A predicate idiom ("dabei (sein)", "recht haben") or paren-prefix verb
        # ("(ab)fahren") the grammar cannot cleanly own: its conjugation tail ("ist
        # dabei,", "hat recht,") would otherwise become junk "other" nodes. Swallow the
        # tail into the one (flagged) head record instead - a new clean entry, a noun or
        # a real verb-start, is never a form continuation, so it ends the swallow.
        if (
            swallow
            and pending is not None
            and _is_verb_form_continuation(head)
            and not _A2_VERB_START_RE.match(head)
        ):
            if example:
                example_parts.append(example)
            continue
        # A reflexive verb whose infinitive and "(sich)" wrapped onto separate rows
        # ("entschuldigen" | "(sich)," | "entschuldigt," | "hat entschuldigt"): the bare
        # infinitive was provisionally "other"; the "(sich)" row reveals it as a verb and
        # its conjugation follows as ordinary verb-form continuations.
        if (
            head.startswith("(sich)")
            and pending is not None
            and pending.pos == "other"
            and pending.lemma.isalpha()
        ):
            pending.pos = "verb"
            head_parts = [pending.lemma_raw, head]
            verb_open = True
            if example:
                example_parts.append(example)
            continue
        # A compound noun whose lemma wrapped at a hyphen ("das Einkaufs-" | "zentrum,
        # -en"): merge with the previous noun rather than emit two junk nodes (as A1).
        if (
            pending is not None
            and pending.pos == "noun"
            and pending.lemma.endswith("-")
        ):
            combined = pending.lemma_raw[:-1] + head
            if example:
                example_parts.append(example)
            pending = _classify_entry(combined, "", pending.source_page, level)
            continue

        flush()
        pending, verb_open = _classify_a2_entry(head, example, page_no, level)
        head_parts = [head] if pending.pos == "verb" else []
        # An "other" head that carries a comma, a paren, or a slash is a predicate idiom
        # or dual-form entry; open the swallow so its conjugation tail folds into it.
        swallow = pending.pos == "other" and any(c in head for c in ",/(")
        if pending.pos == "noun":
            example = _reclaim_plural(pending, example)
        example_parts = [example] if example else []

    flush()


def _is_list_start(rows: list[list[dict]]) -> bool:
    """True if a page carries the "Alphabetischer Wortschatz" section heading. The same
    phrase in B1's table of contents is excluded because there it is indented, while the
    heading word sits at the left margin (x0 < the example-zone boundary)."""
    return any(
        _A2_LIST_START in w["text"].lower() and w["x0"] < _A2_LEFT_EX
        for row in rows
        for w in row
    )


def parse_a2_wordlist(pdf_path: Path, level: str = "A2") -> list[WordRecord]:
    """Parse a Goethe two-column alphabetical Wortliste into WordRecords - the A2 list
    whole, or the B1 alphabetical section (pass level="B1"). Parsing starts at the
    "Alphabetischer Wortschatz" heading, skipping the Vorwort and the thematic front
    matter; the back cover is skipped by its non-content header. Each content page is
    split into two column streams folded independently; nouns and "other" reuse the A1
    grammar, verbs use the conjugation accumulator. `level` stamps the CEFR level."""
    records: list[WordRecord] = []
    in_list = False
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            rows = _rows_from_page(page)
            if not rows:
                continue
            if not in_list:
                if _is_list_start(rows):
                    in_list = True
                else:
                    continue
            # Inside the section every page is content, but the back cover (a mirrored
            # page-code header) is not; content pages run the "WORTLISTE" or
            # "(GOETHE-)ZERTIFIKAT" running header (A2 uses the former pair, B1 the
            # latter).
            header = " ".join(w["text"] for w in rows[0])
            if not header.startswith(
                (_A2_PAGE_HEADER, "ZERTIFIKAT", "GOETHE-ZERTIFIKAT")
            ):
                continue
            left, right = _a2_column_streams(rows)
            _consume_a2_column(left, records, page.page_number, level)
            _consume_a2_column(right, records, page.page_number, level)
    return records
