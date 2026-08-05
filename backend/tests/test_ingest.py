"""Tests for app.rag.ingest - the Goethe Wortliste orchestrator (#13).

Two layers, mirroring the parser tests. Pure unit tests cover PDF discovery and the
persist/stats wiring with the parsers monkeypatched, so they run in CI where the
copyrighted source PDFs are gitignored. An integration test ingests the real A1/A2/B1
lists end to end into a temp DB when the PDFs are present, and re-runs to prove the
upsert is idempotent.
"""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.learner.models import Word
from app.rag import ingest as ingest_mod
from app.rag.glossary_parser import WordRecord
from app.rag.ingest import GOETHE_FILES, goethe_pdf_paths, ingest_goethe


def _touch_goethe_tree(root: Path, levels=tuple(GOETHE_FILES)) -> Path:
    """Lay down empty stand-in PDFs at the paths discovery expects."""
    for level in levels:
        p = root / "goethe/wortlisten" / GOETHE_FILES[level]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.4 fake")
    return root


def _rec(lemma, level, *, pos="noun", needs_review=False) -> WordRecord:
    return WordRecord(
        lemma=lemma,
        lemma_raw=lemma,
        pos=pos,
        article="die" if pos == "noun" else None,
        plural="-n" if pos == "noun" else None,
        level=level,
        source_page=1,
        needs_review=needs_review,
    )


# --- discovery ------------------------------------------------------------------


def test_goethe_pdf_paths_resolves_each_level(tmp_path):
    paths = goethe_pdf_paths(_touch_goethe_tree(tmp_path))
    assert set(paths) == {"A1", "A2", "B1"}
    assert paths["A1"].name == "A1_SD1_Wortliste_02.pdf"
    assert all(p.exists() for p in paths.values())


def test_goethe_pdf_paths_raises_on_missing_level(tmp_path):
    # Only A1 and A2 present; a partial ingest would silently under-report B1
    # coverage, so a missing list is a hard error naming the absent path.
    _touch_goethe_tree(tmp_path, levels=["A1", "A2"])
    with pytest.raises(FileNotFoundError) as exc:
        goethe_pdf_paths(tmp_path)
    assert "B1" in str(exc.value)


# --- persist + stats wiring (parsers monkeypatched, no PDF) ----------------------


def test_ingest_goethe_persists_with_source_and_stats(
    tmp_path, db_session, monkeypatch
):
    root = _touch_goethe_tree(tmp_path)
    fake = {
        "A1": [_rec("Flasche", "A1"), _rec("gehen", "A1", pos="other")],
        "A2": [
            _rec("Drucker", "A2"),
            _rec("mies", "A2", pos="other", needs_review=True),
        ],
        "B1": [_rec("Abzug", "B1")],
    }
    monkeypatch.setattr(ingest_mod, "_parse_level", lambda level, path: fake[level])

    stats = ingest_goethe(db_session, root)

    rows = db_session.scalars(select(Word)).all()
    assert len(rows) == 5
    assert {r.source for r in rows} == {"goethe"}  # every row stamped goethe
    assert (
        db_session.scalar(
            select(func.count()).select_from(Word).where(Word.level == "B1")
        )
        == 1
    )

    by_level = {s.level: s for s in stats.levels}
    assert by_level["A1"].parsed == 2 and by_level["A1"].stored == 2
    assert by_level["A1"].needs_review == 0
    assert by_level["A2"].needs_review == 1  # flagged rows counted honestly
    assert stats.total_words == 5  # no homograph collisions in this set
    assert stats.family_edges == 0  # no verbs share an infinitive in this set


def test_ingest_goethe_stats_surface_homograph_merge(tmp_path, db_session, monkeypatch):
    # Two nouns sharing (lemma, pos, level) but differing by gender collapse into one
    # row under the UNIQUE key. Stats must report parsed>stored so the merge is visible,
    # not hidden - the stored count is the honest coverage denominator.
    root = _touch_goethe_tree(tmp_path)
    band_der = _rec("Band", "B1")
    band_das = _rec("Band", "B1")
    band_das.article = "das"
    monkeypatch.setattr(
        ingest_mod,
        "_parse_level",
        lambda level, path: [band_der, band_das] if level == "B1" else [],
    )

    stats = ingest_goethe(db_session, root)

    b1 = next(s for s in stats.levels if s.level == "B1")
    assert (b1.parsed, b1.stored) == (2, 1)
    assert stats.total_words == 1
    assert db_session.scalar(select(func.count()).select_from(Word)) == 1


def test_ingest_goethe_is_idempotent(tmp_path, db_session, monkeypatch):
    root = _touch_goethe_tree(tmp_path)
    fake = {
        "A1": [_rec("Flasche", "A1")],
        "A2": [_rec("Drucker", "A2")],
        "B1": [_rec("Abzug", "B1")],
    }
    monkeypatch.setattr(ingest_mod, "_parse_level", lambda level, path: fake[level])

    ingest_goethe(db_session, root)
    ingest_goethe(db_session, root)
    # Upsert on (lemma, pos, level): the second run updates in place, no duplicates.
    assert db_session.scalar(select(func.count()).select_from(Word)) == 3


def test_write_stats_returns_previous_run_for_diff(tmp_path, db_session, monkeypatch):
    root = _touch_goethe_tree(tmp_path)
    monkeypatch.setattr(
        ingest_mod, "_parse_level", lambda level, path: [_rec("x", level)]
    )
    data_dir = tmp_path / "data"

    stats = ingest_goethe(db_session, root)
    assert ingest_mod._write_stats(stats, data_dir) is None  # first run, nothing prior
    previous = ingest_mod._write_stats(stats, data_dir)
    assert previous["goethe"]["total_words"] == stats.total_words
    assert (data_dir / "ingest_stats.json").exists()


# --- integration against the real Wortlisten ------------------------------------

_BOOKS = Path(__file__).resolve().parents[2] / "Deutsch_Books"
needs_books = pytest.mark.skipif(
    not (_BOOKS / "goethe/wortlisten" / GOETHE_FILES["A1"]).exists(),
    reason="Goethe Wortliste PDFs not present (gitignored)",
)


@needs_books
def test_ingest_goethe_end_to_end(db_session):
    stats = ingest_goethe(db_session, _BOOKS)

    counts = dict(
        db_session.execute(select(Word.level, func.count()).group_by(Word.level)).all()
    )
    assert counts["A1"] > 600 and counts["A2"] > 1000 and counts["B1"] > 2500
    # total_words is distinct stored rows, which is exactly what landed per level.
    assert stats.total_words == sum(counts.values())
    assert (
        db_session.scalar(
            select(func.count()).select_from(Word).where(Word.source != "goethe")
        )
        == 0
    )
    # The needs_review flag must survive persistence, so /graph-check can audit the
    # ambiguous rows rather than have them silently vanish or arrive all-clean.
    flagged_in_db = db_session.scalar(
        select(func.count()).select_from(Word).where(Word.needs_review.is_(True))
    )
    assert flagged_in_db == sum(s.needs_review for s in stats.levels) > 0
    assert stats.family_edges > 0  # separable verbs share an infinitive -> edges


@needs_books
def test_ingest_goethe_end_to_end_idempotent(db_session):
    first = ingest_goethe(db_session, _BOOKS)
    before = db_session.scalar(select(func.count()).select_from(Word))
    second = ingest_goethe(db_session, _BOOKS)
    after = db_session.scalar(select(func.count()).select_from(Word))
    assert before == after == first.total_words == second.total_words
