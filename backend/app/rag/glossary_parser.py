"""Deterministic parser for the Netzwerk neu A1 Deutsch-Englisch glossary PDF (#9).

Scope: the A1 glossary only. The A2/B1 English glossaries have no extractable
text on any page (confirmed by a full per-page scan, docs/feasibility/009-glossary-parser.md)
- every "word" is a raster image there, so they belong on the vision-extraction
track (issue #10), not this regex parser.

Known limitation, not a parser bug: the source PDF's embedded font subset has no
Unicode mapping for the glyph used to mark a stressed short vowel, so words like
"elf" and "fuenf" extract as "lf" and "f nf" with the vowel silently absent from
the text layer - no text-extraction library can recover a character the font
never exposes. Entries with a detected gap are flagged ``needs_review=True``
with the raw extracted string kept in ``lemma_raw`` rather than guessed at.
"""

import re
from pathlib import Path
from typing import Literal

import pdfplumber
from pydantic import BaseModel

_ARTICLES = {"der", "die", "das"}
_ENGLISH_COLUMN_X0 = 250  # words right of this are the English half of the row
_ROW_TOLERANCE = 3  # points; pdfplumber's 'top' jitters a couple points between columns
# Wider than pdfplumber's default (3pt): the font's dropped-vowel gap (see module
# docstring) is otherwise indistinguishable from a real inter-word space and
# splits one word into two, e.g. "fuenf" -> "f", "nf". 7pt merges those back into
# one token without merging distinct short words (verified against the source PDF).
_WORD_X_TOLERANCE = 7
_CHAPTER_RE = re.compile(r"^Kapitel\s+(\d+):\s*(.+)$")
_EXERCISE_ANCHOR_RE = re.compile(r"^(\d+[a-z]?(?:\s+[UÜu]B)?)\s+(.+)$")
_BOILERPLATE_RE = re.compile(
    r"^(Netzwerk neu|Glossar (A1|A2|B1)|Deutsch.*Englisch|"
    r".*Ernst Klett Sprachen.*|Alle Rechte vorbehalten.*|.*Seite \d+)$"
)
# Fixed recap-section title used throughout the Netzwerk neu series; the only
# subheading in the corpus that is lowercase, so it needs an explicit exception
# to the "subheadings start uppercase" rule below.
_KNOWN_LOWERCASE_SUBHEADING = "kurz und klar"
_ARTICLE_NOUN_RE = re.compile(
    r"^(der|die|das)\s+([A-ZÄÖÜ][\wäöüß.\-]*)"
    r'(?:,\s*("?-\S*|\(Pl\.\)|\(Sg\.[^)]*\)))?\s*(.*)$'
)
_VERB_RE = re.compile(
    r"^([\wäöüß]+)(?:\|([\wäöüß]+))?,\s*(?:er|es)\s+([\wäöüß]+)"
    r"(?:,\s*(hat|ist)\s+([\wäöüß]+))?\s*(.*)$"
)


class VerbForm(BaseModel):
    prefix: str | None = None
    infinitive: str
    third_person_present: str | None = None
    perfect_auxiliary: Literal["haben", "sein"] | None = None
    past_participle: str | None = None


class WordRecord(BaseModel):
    lemma: str
    lemma_raw: str
    pos: Literal["noun", "verb", "other"]
    article: Literal["der", "die", "das"] | None = None
    plural: str | None = None
    verb: VerbForm | None = None
    translation_en: str
    chapter: int | None = None
    chapter_title: str | None = None
    topic: str | None = None
    exercise_ref: str | None = None
    level: Literal["A1"] = "A1"
    source_page: int
    needs_review: bool = False


def _rows_from_page(page: "pdfplumber.page.Page") -> list[list[dict]]:
    """Group words into visual rows, tolerant of a few points of 'top' jitter."""
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


def _split_columns(row: list[dict]) -> tuple[str, str]:
    german = " ".join(w["text"] for w in row if w["x0"] < _ENGLISH_COLUMN_X0)
    english = " ".join(w["text"] for w in row if w["x0"] >= _ENGLISH_COLUMN_X0)
    return german, english


_VOWELS = set("aeiouäöü")


def _has_stress_gap(text: str) -> bool:
    """Detects only the subset of the font's dropped-vowel bug (module docstring)
    where the missing vowel leaves a token with zero vowels at all, e.g. "lf"
    (elf), "fnf" (fuenf), "nll" (null) - a high-precision, incomplete-recall
    proxy. Partial corruption that still leaves a vowel elsewhere in the word
    (e.g. "Artkel" for "Artikel") is not caught here; the gold-sample eval
    (tests/test_glossary_parser.py) measures the true corruption rate."""
    for token in re.findall(r"[a-zäöüß]+", text.lower()):
        if len(token) >= 2 and not any(c in _VOWELS for c in token):
            return True
    return False


def _parse_entry(
    german: str,
    english: str,
    page_no: int,
    chapter: int | None,
    chapter_title: str | None,
    topic: str | None,
    exercise_ref: str | None,
) -> WordRecord:
    german = german.strip()
    english = english.strip()
    needs_review = _has_stress_gap(german)

    m = _ARTICLE_NOUN_RE.match(german)
    if m:
        article, noun, plural_raw, _rest = m.groups()
        plural = plural_raw.strip() if plural_raw else None
        return WordRecord(
            lemma=noun,
            lemma_raw=german,
            pos="noun",
            article=article,
            plural=plural,
            translation_en=english,
            chapter=chapter,
            chapter_title=chapter_title,
            topic=topic,
            exercise_ref=exercise_ref,
            source_page=page_no,
            needs_review=needs_review,
        )

    m = _VERB_RE.match(german)
    if m:
        infinitive, prefix_part, third_person, aux_raw, participle, _rest = m.groups()
        # separable-prefix verbs are written prefix|stem, e.g. "mit|sprechen"
        prefix, infinitive = (
            (infinitive, prefix_part) if prefix_part else (None, infinitive)
        )
        aux: Literal["haben", "sein"] | None = None
        if aux_raw == "hat":
            aux = "haben"
        elif aux_raw == "ist":
            aux = "sein"
        return WordRecord(
            lemma=(prefix + "|" + infinitive) if prefix else infinitive,
            lemma_raw=german,
            pos="verb",
            verb=VerbForm(
                prefix=prefix,
                infinitive=infinitive,
                third_person_present=third_person,
                perfect_auxiliary=aux,
                past_participle=participle,
            ),
            translation_en=english,
            chapter=chapter,
            chapter_title=chapter_title,
            topic=topic,
            exercise_ref=exercise_ref,
            source_page=page_no,
            needs_review=needs_review,
        )

    lemma = german.split(",")[0].split("(")[0].strip() or german
    return WordRecord(
        lemma=lemma,
        lemma_raw=german,
        pos="other",
        translation_en=english,
        chapter=chapter,
        chapter_title=chapter_title,
        topic=topic,
        exercise_ref=exercise_ref,
        source_page=page_no,
        needs_review=needs_review,
    )


def parse_glossary(pdf_path: Path) -> list[WordRecord]:
    """Parse the A1 Netzwerk neu Deutsch-Englisch glossary into WordRecords.

    Every page, including page 1, carries real chapter entries in this file
    (unlike some other glossaries in the corpus); boilerplate lines (title,
    copyright footer) are filtered by content, not by page number. Multi-line
    entries (verb participle wraps, parenthetical usage examples) are re-joined
    by tracking unclosed parens and rows missing one column, since neither
    signal alone is reliable.
    """
    records: list[WordRecord] = []
    chapter: int | None = None
    chapter_title: str | None = None
    topic: str | None = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_no = page.page_number
            buf_de: list[str] = []
            buf_en: list[str] = []
            buf_exercise: str | None = None
            open_parens = 0

            def flush(page_no, chapter, chapter_title, topic):
                nonlocal buf_de, buf_en, buf_exercise
                if buf_de:
                    records.append(
                        _parse_entry(
                            " ".join(buf_de),
                            " ".join(buf_en),
                            page_no,
                            chapter,
                            chapter_title,
                            topic,
                            buf_exercise,
                        )
                    )
                buf_de, buf_en, buf_exercise = [], [], None

            for row in _rows_from_page(page):
                german, english = _split_columns(row)
                if not german and not english:
                    continue

                full_line = f"{german} {english}".strip()
                if _BOILERPLATE_RE.match(full_line) or _BOILERPLATE_RE.match(german):
                    continue
                chap_m = _CHAPTER_RE.match(full_line)
                if chap_m:
                    flush(page_no, chapter, chapter_title, topic)
                    open_parens = 0
                    chapter, chapter_title = int(chap_m.group(1)), chap_m.group(2)
                    topic = None
                    continue

                if (
                    open_parens <= 0
                    and german
                    and not english
                    and (german[0].isupper() or german == _KNOWN_LOWERCASE_SUBHEADING)
                ):
                    # standalone German-only line with no continuation pending:
                    # a topic subheading, e.g. "Laender und Sprachen"
                    flush(page_no, chapter, chapter_title, topic)
                    topic = german
                    continue

                anchor_m = (
                    _EXERCISE_ANCHOR_RE.match(german) if open_parens <= 0 else None
                )
                if open_parens <= 0 and (anchor_m or english):
                    flush(page_no, chapter, chapter_title, topic)
                    if anchor_m:
                        buf_exercise, german = anchor_m.groups()

                buf_de.append(german)
                if english:
                    buf_en.append(english)
                open_parens += full_line.count("(") - full_line.count(")")

            flush(page_no, chapter, chapter_title, topic)

    return records
