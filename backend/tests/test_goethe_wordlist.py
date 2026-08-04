"""Accuracy tests for app.rag.goethe_wordlist (#13, A1 increment).

Two layers, mirroring #9's discipline of measuring rather than asserting:

  - Pure unit tests on `_classify_entry` run everywhere, including CI where the
    copyrighted source PDF is gitignored. They pin the grammar (article/plural/POS
    extraction, reflexive detection, the empty-headword failure flag) directly.
  - Integration tests parse real pages of the A1 Wortliste and measure per-field
    precision + entry segmentation against a hand-labeled gold sample. They skip
    when the PDF is absent.

The A1 list carries no English and no verb conjugation, so translation_en is always
None and pos is "noun" (via the article) / "verb" (only the "(sich)" reflexive
marker) / "other" - the list gives no signal to distinguish a bare verb from an
adverb, and the parser prefers "other" to a guess.
"""

from pathlib import Path

import pytest

from app.rag.goethe_wordlist import (
    _WRAPPED_PLURAL_RE,
    _classify_entry,
    parse_a1_wordlist,
)

# --- pure grammar tests (no PDF, always run) ------------------------------------


def test_classify_noun_with_plural():
    r = _classify_entry("die Flasche, -n", "Eine Flasche Bier, bitte.", 15)
    assert (r.pos, r.article, r.lemma, r.plural) == ("noun", "die", "Flasche", "-n")
    assert r.example_de == "Eine Flasche Bier, bitte."
    assert r.translation_en is None  # Goethe lists are German-only
    assert r.level == "A1"


def test_classify_noun_without_plural():
    r = _classify_entry("das Fleisch", "Fleisch mag ich nicht.", 15)
    assert (r.pos, r.article, r.lemma, r.plural) == ("noun", "das", "Fleisch", None)


def test_classify_noun_umlaut_plural_keeps_comma_spec():
    # "der Fuß, -ü, e" - the plural spec itself contains a comma; because x-position
    # already isolated the headword zone from the example, the whole spec is captured.
    r = _classify_entry("der Fuß, -ü, e", "Der linke Fuß tut mir weh.", 23)
    assert (r.pos, r.article, r.lemma, r.plural) == ("noun", "der", "Fuß", "-ü, e")


def test_classify_noun_plural_only_marker():
    r = _classify_entry("die Geschwister (pl.)", "Ich habe keine Geschwister.", 16)
    assert (r.pos, r.lemma, r.plural) == ("noun", "Geschwister", "(pl.)")


def test_classify_noun_no_change_plural_dash():
    r = _classify_entry("das Hähnchen, -", "Ein Hähnchen mit Pommes bitte!", 16)
    assert (r.pos, r.lemma, r.plural) == ("noun", "Hähnchen", "-")


def test_classify_reflexive_is_verb():
    r = _classify_entry("(sich) freuen", "Ich freue mich auf den Urlaub.", 15)
    assert (r.pos, r.lemma) == ("verb", "freuen")


def test_classify_bare_headword_is_other():
    # No article, no reflexive marker -> "other"; the list can't prove it's a verb.
    for head, lemma in [("frei", "frei"), ("für", "für"), ("an sein", "an sein")]:
        r = _classify_entry(head, "irgendein Beispiel.", 15)
        assert (r.pos, r.lemma) == ("other", lemma)


def test_classify_empty_headword_flags_review():
    r = _classify_entry("", "orphan example", 15)
    assert r.needs_review is True and r.lemma == ""


def test_classify_missing_example_is_none_not_empty():
    r = _classify_entry("die Sehenswürdigkeit, -en", "", 23)
    assert r.example_de is None  # example arrives on a later continuation row


def test_classify_bare_sich_is_verb():
    # The source writes some reflexives without parentheses ("sich kümmern").
    r = _classify_entry("sich kümmern", "Jede Mutter kümmert sich um ihre Kinder.", 20)
    assert (r.pos, r.lemma) == ("verb", "kümmern")


def test_classify_lone_sich_stays_other():
    # "sich" alone is the pronoun entry, not a verb - no following word to take.
    r = _classify_entry("sich", "Sie müssen sich erst anmelden.", 23)
    assert (r.pos, r.lemma) == ("other", "sich")


def test_classify_paired_slash_plural_flags_review():
    # "die Ehefrau, -en/" is half of an "X / Y" pair; the "/" is dropped and flagged.
    r = _classify_entry("die Ehefrau, -en/", "", 8)
    assert (r.pos, r.plural, r.needs_review) == ("noun", "-en", True)


def test_wrapped_plural_re_accepts_hyphen_and_diaeresis_leads():
    # A wrapped plural marker leads with a hyphen ("-en") or a bare diaeresis ("¨e" ->
    # umlaut plural). Both must be reclaimed onto the noun; a diaeresis-only marker used
    # to slip past the hyphen-only pattern and lodge silently at the head of the example.
    for marker, plural, remainder in [
        ("-en", "-en", None),
        ("¨e", "¨e", None),
        ("-en Welche Sehenswürdigkeiten?", "-en", "Welche Sehenswürdigkeiten?"),
        ("¨er Die Bücher sind neu.", "¨er", "Die Bücher sind neu."),
    ]:
        m = _WRAPPED_PLURAL_RE.match(marker)
        assert m is not None, f"marker {marker!r} not recognized"
        assert m.group(1) == plural
        assert m.group(2) == remainder


def test_classify_nounlike_other_flags_review():
    # An article-less but noun-shaped head the article rule can't own is flagged.
    for head in ["Satz, -ä, e", "der/die Bekannte, -n"]:
        r = _classify_entry(head, "irgendein Beispiel.", 23)
        assert r.pos == "other" and r.needs_review is True


# --- integration tests against the real A1 Wortliste ----------------------------

PDF_PATH = (
    Path(__file__).resolve().parents[2]
    / "Deutsch_Books/goethe/wortlisten/A1/A1_SD1_Wortliste_02.pdf"
)
needs_pdf = pytest.mark.skipif(
    not PDF_PATH.exists(), reason="Goethe A1 Wortliste PDF not present (gitignored)"
)

# Hand-labeled against page 15 of the PDF, one entry per row in parser order,
# verified against pdfplumber's raw extraction. plural is None where the source
# gives no plural marker; article is None for non-nouns.
GOLD_PAGE_15 = [
    {"lemma": "Flasche", "pos": "noun", "article": "die", "plural": "-n"},
    {"lemma": "Fleisch", "pos": "noun", "article": "das", "plural": None},
    {"lemma": "fliegen", "pos": "other", "article": None, "plural": None},
    {"lemma": "abfliegen", "pos": "other", "article": None, "plural": None},
    {"lemma": "Abflug", "pos": "noun", "article": "der", "plural": None},
    {"lemma": "Formular", "pos": "noun", "article": "das", "plural": "-e"},
    {"lemma": "Foto", "pos": "noun", "article": "das", "plural": "-s"},
    {"lemma": "Frage", "pos": "noun", "article": "die", "plural": "-n"},
    {"lemma": "Frau", "pos": "noun", "article": "die", "plural": "-en"},
    {"lemma": "frei", "pos": "other", "article": None, "plural": None},
    {"lemma": "freuen", "pos": "verb", "article": None, "plural": None},
    {"lemma": "Fuß", "pos": "noun", "article": "der", "plural": "-ü, e"},
    {"lemma": "Gast", "pos": "noun", "article": "der", "plural": "-ä, e"},
]
# Independent full hand-count of page 15's alphabetical entries, guarding entry
# segmentation (continuation-row joining must not over- or under-split).
PAGE_15_TRUE_ENTRY_COUNT = 37
MIN_FIELD_PRECISION = 0.95


@pytest.fixture(scope="module")
def records():
    return parse_a1_wordlist(PDF_PATH)


@pytest.fixture(scope="module")
def page_15(records):
    return [r for r in records if r.source_page == 15]


@needs_pdf
def test_page_15_entry_count(page_15):
    assert len(page_15) == PAGE_15_TRUE_ENTRY_COUNT, (
        f"expected {PAGE_15_TRUE_ENTRY_COUNT} entries on page 15, got {len(page_15)} "
        "- continuation-row joining regressed"
    )


@needs_pdf
def test_page_15_field_precision(page_15):
    by_lemma = {r.lemma: r for r in page_15}
    fields = ["pos", "article", "plural"]
    results = {}
    for field in fields:
        correct = total = 0
        for gold in GOLD_PAGE_15:
            actual = by_lemma.get(gold["lemma"])
            if actual is None:
                total += 1
                continue
            total += 1
            correct += getattr(actual, field) == gold[field]
        results[field] = (correct, total, correct / total if total else 1.0)
    report = "\n".join(f"  {f}: {c}/{t} ({r:.0%})" for f, (c, t, r) in results.items())
    print(
        f"\ngoethe_wordlist field precision (page 15, n={len(GOLD_PAGE_15)}):\n{report}"
    )
    for field, (correct, total, rate) in results.items():
        assert rate >= MIN_FIELD_PRECISION, (
            f"{field} precision {rate:.0%} ({correct}/{total}) below "
            f"{MIN_FIELD_PRECISION:.0%}"
        )


@needs_pdf
def test_multi_sentence_example_is_joined(page_15):
    # "die Frau" carries three example rows; all must be joined into one example.
    frau = next(r for r in page_15 if r.lemma == "Frau")
    assert frau.example_de == (
        "Das ist Frau Becker. Guten Tag, Frau Schmitt! "
        "Hier arbeiten mehr Frauen als Männer."
    )


@needs_pdf
def test_no_boilerplate_bleeds_into_examples(records):
    # Running head ("INVeNTAre") and footer ("Seite N") sit in the example zone;
    # none may survive into an entry's example.
    for r in records:
        if r.example_de:
            assert "INVeNTAre" not in r.example_de
            assert "Seite " not in r.example_de


@needs_pdf
def test_front_and_back_matter_excluded(records):
    # Alphabetical list is pages 9-27; the Inventar/Wortgruppen front matter and the
    # Literatur bibliography must never become word nodes.
    pages = {r.source_page for r in records}
    assert min(pages) >= 9 and max(pages) <= 27
    assert not any("=" in r.lemma for r in records)  # number tables were excluded


@needs_pdf
def test_hyphen_wrapped_compound_is_merged(records):
    # "der Anruf-" + "beantworter" is one word; the fragments must not survive.
    by_lemma = {r.lemma: r for r in records}
    assert "Anrufbeantworter" in by_lemma
    assert "Lebensmittel" in by_lemma
    for fragment in ("Anruf-", "beantworter", "Lebens-", "mittel (pl.)"):
        assert fragment not in by_lemma


@needs_pdf
def test_wrapped_plural_reclaimed_from_example(records):
    # "die Sehenswürdigkeit," | "-en" - the marker wrapped into the example zone and
    # must land on the plural, not at the head of the example sentence.
    sw = next(r for r in records if r.lemma == "Sehenswürdigkeit")
    assert sw.plural == "-en"
    assert sw.example_de.startswith("Welche Sehenswürdigkeiten")


@needs_pdf
def test_review_flags_are_the_ambiguous_rows(records):
    flagged = {r.lemma for r in records if r.needs_review}
    # the paired-entry halves and the article-less noun-like heads, and nothing more
    assert {"Ehefrau", "Hausfrau", "Partner"} <= flagged
    assert len(flagged) <= 8


@needs_pdf
def test_full_document_parses_clean_and_fast():
    import time

    start = time.monotonic()
    records = parse_a1_wordlist(PDF_PATH)
    elapsed = time.monotonic() - start
    assert len(records) > 600  # ~686 today; sanity floor for the A1 vocabulary
    assert elapsed < 30, f"parse took {elapsed:.1f}s, expected well under 30s"
    assert all(r.level == "A1" and r.translation_en is None for r in records)
    # A handful of genuinely ambiguous source rows (paired "X / Y" entries,
    # article-less noun-like heads) are flagged, not silently trusted; the rest parse
    # clean. The floor stays low so a segmentation regression that mass-flags is caught.
    assert sum(r.needs_review for r in records) <= 8
