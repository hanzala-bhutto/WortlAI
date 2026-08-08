"""Accuracy tests for app.rag.goethe_wordlist (#13, A1 + A2 + B1 increments).

Two layers, mirroring #9's discipline of measuring rather than asserting:

  - Pure unit tests on `_classify_entry` (A1 grammar) and `_parse_verb_forms` /
    `_is_verb_form_continuation` (A2/B1 verb conjugation, including B1's preterite row)
    run everywhere, including CI where the copyrighted source PDFs are gitignored.
  - Integration tests parse real pages of the A1, A2, and B1 Wortlisten. A1 measures
    per-field precision against a hand-labeled gold sample; A2 and B1 assert the
    invariant that no unflagged record is junk (messy rows must be flagged, never
    silently wrong). Each skips when its PDF is absent.

Goethe lists carry no English, so translation_en is always None. The A1 list has no
verb conjugation (verb only via "(sich)"); the A2/B1 lists do, and their verbs carry a
full VerbForm. B1's alphabetical section is parsed by the same two-column engine as A2;
its leading thematic (WORTGRUPPEN) section is parsed separately into Word.topic by
`parse_b1_topics` (#71). Genuinely ambiguous rows are flagged, not guessed.
"""

from pathlib import Path

import pytest

from app.rag.goethe_wordlist import (
    _A2_VERB_START_RE,
    _WRAPPED_PLURAL_RE,
    _a2_topic_record,
    _classify_entry,
    _classify_thematic_cell,
    _is_verb_form_continuation,
    _is_wortgruppen_start,
    _parse_verb_forms,
    _thematic_cells,
    _topic_for_header,
    parse_a1_wordlist,
    parse_a2_topics,
    parse_a2_wordlist,
    parse_b1_topics,
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


def test_parse_verb_forms_b1_preterite_row():
    # B1 lists a preterite and shares its row with the perfect ("bog ab, ist
    # abgebogen"); the perfect is still found and the preterite is dropped (no field).
    v = _parse_verb_forms("abbiegen, biegt ab, bog ab, ist abgebogen")
    assert (v.infinitive, v.third_person_present, v.prefix) == (
        "abbiegen",
        "biegt ab",
        "ab",
    )
    assert (v.perfect_auxiliary, v.past_participle) == ("sein", "abgebogen")


def test_parse_verb_forms_auxiliary_verb_haben():
    # haben's own 3rd-person is "hat", which must not be mistaken for the perfect "hat
    # gehabt" - the perfect requires a participle after the auxiliary.
    v = _parse_verb_forms("haben, hat, hatte, hat gehabt")
    assert v.third_person_present == "hat"
    assert (v.perfect_auxiliary, v.past_participle) == ("haben", "gehabt")


def test_is_verb_form_continuation():
    for cont in [
        "hat gedruckt",
        "druckt,",
        "lädt ein,",
        "durfte,",
        "ist eingestiegen",
        "bog ab, ist abgebogen",  # B1 preterite+perfect on one row
    ]:
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


# --- B1: same two-column engine over the B1 alphabetical section -----------------

B1_PDF_PATH = (
    Path(__file__).resolve().parents[2]
    / "Deutsch_Books/goethe/wortlisten/B1/Goethe-Zertifikat_B1_Wortliste.pdf"
)
needs_b1_pdf = pytest.mark.skipif(
    not B1_PDF_PATH.exists(), reason="Goethe B1 Wortliste PDF not present (gitignored)"
)


@pytest.fixture(scope="module")
def b1_records():
    return parse_a2_wordlist(B1_PDF_PATH, level="B1")


@needs_b1_pdf
def test_b1_alphabetical_section_only(b1_records):
    # The alphabetical section is pages 16-103; the leading WORTGRUPPEN thematic section
    # (parsed separately by parse_b1_topics, #71) and the intro/TOC must be excluded here.
    # The TOC repeats the "Alphabetischer Wortschatz" phrase - the margin guard must
    # not let it flip the section on early and drag the thematic pages in.
    pages = {r.source_page for r in b1_records}
    assert min(pages) >= 16 and max(pages) <= 103
    assert len(b1_records) > 2500  # ~3242 today; B1 is the largest list
    assert all(r.level == "B1" for r in b1_records)


@needs_b1_pdf
def test_b1_no_silent_junk(b1_records):
    silent = [r for r in b1_records if not r.needs_review and not _is_clean_lemma(r)]
    assert silent == [], (
        f"{len(silent)} unflagged junk: {[r.lemma for r in silent][:10]}"
    )
    rate = sum(r.needs_review for r in b1_records) / len(b1_records)
    assert rate < 0.10, (
        f"needs_review rate {rate:.1%} too high - segmentation regressed"
    )


@needs_b1_pdf
def test_b1_preterite_verb_keeps_perfect(b1_records):
    # "abbiegen, biegt ab, bog ab, ist abgebogen" - the preterite+perfect share a row;
    # the perfect must land on the verb, not leak as a separate "bog ab, ..." node.
    abbiegen = next(r for r in b1_records if r.lemma == "abbiegen")
    assert abbiegen.pos == "verb"
    assert abbiegen.verb.perfect_auxiliary == "sein"
    assert abbiegen.verb.past_participle == "abgebogen"
    assert abbiegen.verb.prefix == "ab"
    assert not any(r.lemma.startswith("bog ab") for r in b1_records)


# --- thematic WORTGRUPPEN stage (#71): pure unit tests (no PDF) ------------------


def _row(*tokens):
    """A visual row as the word dicts _thematic_cells reads (it only needs `text`)."""
    return [{"text": t} for t in tokens]


def test_topic_for_header_maps_whitelisted_groups():
    assert _topic_for_header("TIERE") == "Tiere"
    assert _topic_for_header("politische begriffe") == "Politische Begriffe"
    # The long header resolves off its first word.
    assert (
        _topic_for_header("LÄNDER, KONTINENTE, NATIONALITÄTEN (STAATSANGEHÖRIGKEITEN)")
        == "Länder und Nationalitäten"
    )


def test_topic_for_header_rejects_reference_data_groups():
    # Reference-data groups are deliberately off the whitelist; a near-miss prefix
    # ("BILDUNG:" vs "BILDUNGSEINRICHTUNGEN") must not slip through.
    assert _topic_for_header("ZAHLEN, BRUCHZAHLEN") is None
    assert _topic_for_header("BILDUNG: SCHULFÄCHER") is None
    assert _topic_for_header("FARBEN") is None


def test_thematic_cells_splits_a_three_column_row_at_the_articles():
    # A TIERE row runs three columns; each noun cell is led by an article, and the
    # leading region label / stray tokens before the first article are dropped.
    cells = _thematic_cells(
        _row("der", "Affe,", "-n", "der", "Hase,", "-n", "die", "Mücke,", "-n")
    )
    assert cells == ["der Affe, -n", "der Hase, -n", "die Mücke, -n"]


def test_thematic_cells_drops_a_row_with_no_article():
    assert _thematic_cells(_row("Biologie", "Musik")) == []


def test_classify_thematic_clean_noun():
    rec = _classify_thematic_cell("der Affe, -n", "Tiere", 13, "B1")
    assert (rec.pos, rec.article, rec.lemma, rec.plural) == (
        "noun",
        "der",
        "Affe",
        "-n",
    )
    assert rec.topic == "Tiere"
    assert rec.example_de is None and rec.level == "B1"
    assert rec.needs_review is False


def test_classify_thematic_trailing_derived_form_is_not_a_plural():
    # "der Norden Nord-/nördlich": the slash is in a derived-form annotation, not the
    # noun, so the lemma is clean, the plural is dropped, and nothing is flagged.
    rec = _classify_thematic_cell(
        "der Norden Nord-/nördlich", "Himmelsrichtungen", 11, "B1"
    )
    assert (rec.lemma, rec.plural, rec.needs_review) == ("Norden", None, False)


def test_classify_thematic_slash_compound_is_flagged():
    # A slash glued to the noun marks a multi-lemma cell; the first lemma is kept but
    # the record is flagged, since the rest is not captured.
    rec = _classify_thematic_cell(
        "die Krippe/der Kindergarten/die Kindertagesstätte",
        "Bildungseinrichtungen",
        10,
        "B1",
    )
    assert rec.lemma == "Krippe" and rec.needs_review is True


def test_classify_thematic_flags_non_marker_comma_tail():
    # A comma-tail that is a second lemma, not a plural ("die Realschule, Sekundarschule"
    # on a Swiss school-type list), is flagged - and the tail is NOT stored as a plural.
    rec = _classify_thematic_cell(
        "die Realschule, Sekundarschule", "Bildungseinrichtungen", 10, "B1"
    )
    assert rec.lemma == "Realschule"
    assert rec.plural is None and rec.needs_review is True


def test_classify_thematic_keeps_diaeresis_plural():
    # The PDF writes an umlaut plural as a standalone diaeresis ("¨-e"); it is a valid
    # marker, kept as the plural and not flagged.
    rec = _classify_thematic_cell("die Kuh, ¨-e", "Tiere", 13, "B1")
    assert (rec.lemma, rec.plural, rec.needs_review) == ("Kuh", "¨-e", False)


def test_classify_thematic_rejects_non_noun_cells():
    # An adjective and a parenthesised head are not clean article-nouns -> dropped.
    assert (
        _classify_thematic_cell(
            "die (Fach-)Hochschule", "Bildungseinrichtungen", 10, "B1"
        )
        is None
    )
    assert (
        _classify_thematic_cell(
            "der eigenen Herkunft", "Länder und Nationalitäten", 12, "B1"
        )
        is None
    )


# --- thematic stage: integration over the real B1 Wortliste ---------------------

_WHITELIST_LABELS = {
    "Tiere",
    "Politische Begriffe",
    "Himmelsrichtungen",
    "Bildungseinrichtungen",
    "Länder und Nationalitäten",
}


@pytest.fixture(scope="module")
def b1_topics():
    return parse_b1_topics(B1_PDF_PATH)


@needs_b1_pdf
def test_b1_topics_are_whitelisted_nouns_only(b1_topics):
    assert {r.topic for r in b1_topics} <= _WHITELIST_LABELS
    assert all(r.pos == "noun" and r.example_de is None for r in b1_topics)
    assert all(r.level == "B1" for r in b1_topics)
    # TIERE is the pristine reference group; its common animals must be present.
    tiere = {r.lemma for r in b1_topics if r.topic == "Tiere"}
    assert {"Hund", "Katze", "Pferd", "Vogel"} <= tiere


@needs_b1_pdf
def test_b1_topics_exclude_the_alphabetical_section(b1_topics):
    # The thematic section is pages 8-15; the alphabetical Wortschatz (page 16 on) must
    # not bleed in - it would arrive with example sentences the thematic grid never has.
    assert b1_topics, "expected thematic records"
    assert max(r.source_page for r in b1_topics) < 16


@needs_b1_pdf
def test_b1_topics_flag_only_the_messy_compound_cells(b1_topics):
    # The clean noun groups parse pristine; only the slash-compound BILDUNGSEINRICHTUNGEN
    # cells are flagged, so the overall rate stays low and TIERE is spotless.
    rate = sum(r.needs_review for r in b1_topics) / len(b1_topics)
    assert rate < 0.15, f"needs_review rate {rate:.1%} too high"
    assert not any(r.needs_review for r in b1_topics if r.topic == "Tiere")


# --- A2 front-matter thematic tables (#71): pure unit tests (no PDF) -------------


def _wrow(*pairs):
    """A row of word dicts with x0/top, for the column-aware A2 helpers."""
    return [{"text": t, "x0": x, "top": 100} for x, t in pairs]


def _col(*tokens):
    """A single A2 column cell (tokens already isolated to one column)."""
    return [{"text": t, "x0": 359, "top": 200} for t in tokens]


def test_is_wortgruppen_start_needs_the_margin_heading():
    # The margin heading triggers; the indented TOC line with the same word does not.
    assert _is_wortgruppen_start([_wrow((89, "WORTGRUPPEN"), (89, "Abkürzungen"))])
    assert not _is_wortgruppen_start([_wrow((143, "Wortgruppen"), (501, "5"))])


def test_a2_topic_record_article_less_noun():
    rec = _a2_topic_record(_col("Klasse,", "-n"), "Schule und Schulfächer", 6)
    assert (rec.pos, rec.article, rec.lemma, rec.plural) == (
        "noun",
        None,
        "Klasse",
        "-n",
    )
    assert rec.topic == "Schule und Schulfächer" and rec.level == "A2"
    assert rec.example_de is None and rec.needs_review is False


def test_a2_topic_record_flags_pair_but_keeps_primary_lemma():
    # A masc/fem pair (either "/" or ";" separated) keeps the primary lemma, flagged.
    slash = _a2_topic_record(
        _col("Bäcker,", "-", "/", "Bäckerin,", "-nen"), "Berufe", 5
    )
    assert slash.lemma == "Bäcker" and slash.needs_review is True
    semi = _a2_topic_record(_col("Autor,", "-en;", "Autorin,", "-nen"), "Berufe", 5)
    assert semi.lemma == "Autor" and semi.needs_review is True


def test_a2_topic_record_glued_plural_and_continuation_row():
    # "Babysitter,-" carries its plural glued to the lemma token.
    rec = _a2_topic_record(_col("Babysitter,-"), "Berufe", 5)
    assert (rec.lemma, rec.plural) == ("Babysitter", "-")
    # A wrapped continuation row leads lowercase and is not an entry.
    assert _a2_topic_record(_col("ter,", "-n"), "Berufe", 5) is None


def test_a2_topic_record_captures_diaeresis_plural():
    # The umlaut plural is written as a standalone diaeresis ("Stundenplan, ¨-e"); it must
    # be captured from the next token, not silently dropped.
    rec = _a2_topic_record(_col("Stundenplan,", "¨-e"), "Schule und Schulfächer", 6)
    assert rec.plural == "¨-e" and rec.needs_review is False


# --- A2 thematic tables: integration over the real A2 Wortliste -----------------


@pytest.fixture(scope="module")
def a2_topics():
    return parse_a2_topics(A2_PDF_PATH)


@needs_a2_pdf
def test_a2_topics_are_whitelisted_nouns_only(a2_topics):
    assert {r.topic for r in a2_topics} <= {"Berufe", "Schule und Schulfächer"}
    assert all(r.pos == "noun" and r.article is None for r in a2_topics)
    assert all(r.example_de is None and r.level == "A2" for r in a2_topics)
    # The school subjects are the pristine reference set here.
    schule = {r.lemma for r in a2_topics if r.topic == "Schule und Schulfächer"}
    assert {"Biologie", "Mathematik", "Sport", "Abitur"} <= schule


@needs_a2_pdf
def test_a2_topics_stop_at_the_next_stacked_group(a2_topics):
    # Each column stacks groups; the whitelisted read must stop at the vertical gap and
    # not swallow the group below it (Himmelsrichtungen under Berufe, dates under Schule).
    lemmas = {r.lemma for r in a2_topics}
    assert not ({"Norden", "Himmelsrichtungen", "Januar", "Osten"} & lemmas)
    # The flagged profession pairs still contribute their primary (masculine) lemma.
    assert {"Arzt", "Lehrer", "Autor"} <= lemmas
