"""Accuracy tests for app.rag.goethe_wordlist (#13, A1 + A2 increments).

Two layers, mirroring #9's discipline of measuring rather than asserting:

  - Pure unit tests on `_classify_entry` (A1 grammar) and `_parse_verb_forms` /
    `_is_verb_form_continuation` (A2 verb conjugation) run everywhere, including CI
    where the copyrighted source PDFs are gitignored.
  - Integration tests parse real pages of the A1 and A2 Wortlisten. A1 measures
    per-field precision against a hand-labeled gold sample; A2 asserts the invariant
    that no unflagged record is junk (messy rows must be flagged, never silently
    wrong). Both skip when their PDF is absent.

Goethe lists carry no English, so translation_en is always None. The A1 list has no
verb conjugation (verb only via "(sich)"); the A2 list does, and its verbs carry a
full VerbForm. Genuinely ambiguous rows are flagged, not guessed.
"""

from pathlib import Path

import pytest

from app.rag.goethe_wordlist import (
    _A2_VERB_START_RE,
    _WRAPPED_PLURAL_RE,
    _classify_entry,
    _is_verb_form_continuation,
    _parse_verb_forms,
    parse_a1_wordlist,
    parse_a2_wordlist,
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


# --- A2 two-column grammar: pure verb-conjugation tests (no PDF) -----------------


def test_parse_verb_forms_simple():
    v = _parse_verb_forms("drucken, druckt, hat gedruckt")
    assert v.infinitive == "drucken"
    assert v.third_person_present == "druckt"
    assert (v.perfect_auxiliary, v.past_participle, v.prefix) == (
        "haben",
        "gedruckt",
        None,
    )


def test_parse_verb_forms_separable_reads_prefix():
    v = _parse_verb_forms("einladen, lädt ein, hat eingeladen")
    assert (v.infinitive, v.third_person_present, v.prefix) == (
        "einladen",
        "lädt ein",
        "ein",
    )
    assert (v.perfect_auxiliary, v.past_participle) == ("haben", "eingeladen")


def test_parse_verb_forms_sein_auxiliary():
    v = _parse_verb_forms("einsteigen, steigt ein, ist eingestiegen")
    assert v.perfect_auxiliary == "sein" and v.past_participle == "eingestiegen"


def test_parse_verb_forms_modal_ignores_preterite():
    # "dürfen, darf, durfte, hat gedurft" - the preterite piece has no field, so the
    # 3rd-person is still darf (not durfte) and the perfect is still read.
    v = _parse_verb_forms("dürfen, darf, durfte, hat gedurft")
    assert (v.infinitive, v.third_person_present) == ("dürfen", "darf")
    assert (v.perfect_auxiliary, v.past_participle) == ("haben", "gedurft")


def test_parse_verb_forms_strips_reflexive_marker():
    v = _parse_verb_forms("duschen (sich), duscht, hat geduscht")
    assert v.infinitive == "duschen" and v.third_person_present == "duscht"


def test_is_verb_form_continuation():
    for cont in ["hat gedruckt", "druckt,", "lädt ein,", "durfte,", "ist eingestiegen"]:
        assert _is_verb_form_continuation(cont) is True
    for new in ["der Drucker, -", "dumm", "die Ecke, -n"]:
        assert _is_verb_form_continuation(new) is False


def test_a2_verb_start_re():
    for start in ["drucken,", "duschen (sich),", "dürfen, darf,", "enden, endet,"]:
        assert _A2_VERB_START_RE.match(start), start
    for notstart in ["der Drucker, -", "dumm", "eigen-", "(ab)fahren,"]:
        assert not _A2_VERB_START_RE.match(notstart), notstart


# --- A2 integration against the real two-column Wortliste ------------------------

A2_PDF_PATH = (
    Path(__file__).resolve().parents[2]
    / "Deutsch_Books/goethe/wortlisten/A2/Goethe-Zertifikat_A2_Wortliste.pdf"
)
needs_a2_pdf = pytest.mark.skipif(
    not A2_PDF_PATH.exists(), reason="Goethe A2 Wortliste PDF not present (gitignored)"
)


@pytest.fixture(scope="module")
def a2_records():
    return parse_a2_wordlist(A2_PDF_PATH)


def _is_clean_lemma(rec):
    """A parsed lemma that is not a wrapped-idiom fragment: no stray comma/paren, no
    auxiliary lead, and a verb infinitive is a single word."""
    lemma = rec.lemma
    if not lemma or "," in lemma or "(" in lemma:
        return False
    parts = lemma.split()
    # A multi-word "other" that opens with an auxiliary is a conjugation fragment
    # ("hat recht", "ist dabei"); the bare auxiliaries haben/sein are themselves
    # legitimate single-word verb entries, so only the multi-word case is junk.
    if len(parts) > 1 and parts[0] in ("hat", "ist", "war", "sind", "haben", "hatte"):
        return False
    return not (rec.pos == "verb" and " " in lemma)


@needs_a2_pdf
def test_a2_front_and_back_matter_excluded(a2_records):
    # The alphabetical section is pages 8-31; the WORTGRUPPEN thematic tables and the
    # Uhrzeit/Zahlen front matter (pages 5-7) and the back cover must never appear.
    pages = {r.source_page for r in a2_records}
    assert min(pages) >= 8 and max(pages) <= 31
    assert len(a2_records) > 1000  # ~1233 today; the two-column list is large
    assert all(r.level == "A2" and r.translation_en is None for r in a2_records)


@needs_a2_pdf
def test_a2_no_silent_junk(a2_records):
    # The invariant that matters: every record that is NOT flagged needs_review has a
    # clean lemma. Messy source rows (predicate idioms, dual-gender nouns, prefix
    # tables) are allowed, but only when honestly flagged - never silently wrong.
    silent = [r for r in a2_records if not r.needs_review and not _is_clean_lemma(r)]
    assert silent == [], (
        f"{len(silent)} unflagged junk lemmas: {[r.lemma for r in silent][:10]}"
    )
    rate = sum(r.needs_review for r in a2_records) / len(a2_records)
    assert rate < 0.10, (
        f"needs_review rate {rate:.1%} too high - segmentation regressed"
    )


@needs_a2_pdf
def test_a2_separable_verb_conjugation(a2_records):
    einladen = next(r for r in a2_records if r.lemma == "einladen")
    assert einladen.pos == "verb"
    assert einladen.verb.third_person_present == "lädt ein"
    assert einladen.verb.prefix == "ein"
    assert einladen.verb.perfect_auxiliary == "haben"
    assert einladen.verb.past_participle == "eingeladen"


@needs_a2_pdf
def test_a2_nouns_and_compound_merge(a2_records):
    by_lemma = {r.lemma: r for r in a2_records}
    drucker = by_lemma["Drucker"]
    assert (drucker.pos, drucker.article) == ("noun", "der")
    # "das Einkaufs-" | "zentrum, -en" merged across the hyphen wrap, as in A1.
    assert "Einkaufszentrum" in by_lemma
    assert by_lemma["Einkaufszentrum"].article == "das"
    for fragment in ("Einkaufs-", "zentrum"):
        assert fragment not in by_lemma
