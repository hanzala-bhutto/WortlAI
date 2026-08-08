"""Orchestrate source PDFs into the knowledge stores.

Only the Goethe Wortliste path is wired today (#13): discover the A1/A2/B1 lists,
parse each with the deterministic parsers, persist them as ``source="goethe"``, then
derive verb-family edges over the whole words table. The glossary (#9), vision (#10)
and Qdrant (#11) paths are their own issues; this module is the seam they slot into,
so the CLI and stats file are already shaped for more than one source.

Run it from ``backend/``: ``python -m app.rag.ingest --source ../Deutsch_Books``.
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.learner.db import session_scope
from app.rag.glossary_parser import WordRecord
from app.rag.goethe_wordlist import (
    parse_a1_wordlist,
    parse_a2_topics,
    parse_a2_wordlist,
    parse_b1_topics,
)
from app.rag.lexical_graph import (
    TopicStats,
    apply_topics,
    derive_family_edges,
    persist_words,
)

# The canonical Goethe list per level, relative to {source}/goethe/wortlisten/. A1 is
# the Start-Deutsch-1 list the A1 parser targets - deliberately not the Fit1 children's
# variant, whose layout the parser was never calibrated against.
_GOETHE_ROOT = "goethe/wortlisten"
GOETHE_FILES = {
    "A1": "A1/A1_SD1_Wortliste_02.pdf",
    "A2": "A2/Goethe-Zertifikat_A2_Wortliste.pdf",
    "B1": "B1/Goethe-Zertifikat_B1_Wortliste.pdf",
}


@dataclass
class LevelStats:
    level: str
    parsed: int  # records the parser emitted
    stored: int  # distinct rows written - homographs that share (lemma, pos, level)
    needs_review: int  # flagged rows among those stored


@dataclass
class GoetheStats:
    levels: list[LevelStats]
    family_edges: int
    topics: TopicStats  # thematic topics stamped/added (#71)
    # Distinct word rows written: the alphabetical rows per level plus the thematic-only
    # lemmas the topic pass inserted. Below the parsed total because the words UNIQUE key
    # is (lemma, pos, level), so a homograph differing only by gender (der/das Band)
    # merges into one row. Computed as a stable row-id union, so a re-run reports the same
    # number (the topic pass matches its once-inserted rows rather than inserting again).
    total_words: int


def goethe_pdf_paths(source_root: Path) -> dict[str, Path]:
    """Resolve the canonical Goethe list per level under ``source_root``.

    A missing file is a hard error naming the path, not a silent skip: a partial
    ingest would under-report level coverage without any signal that a whole list
    never loaded."""
    paths = {
        level: source_root / _GOETHE_ROOT / rel for level, rel in GOETHE_FILES.items()
    }
    missing = [str(p) for p in paths.values() if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "Goethe Wortliste PDF(s) not found:\n  " + "\n  ".join(missing)
        )
    return paths


def _parse_level(level: str, path: Path) -> list[WordRecord]:
    """Dispatch to the right parser. A1 is single-column; A2 and B1 share the
    two-column engine (B1 over its alphabetical section only)."""
    if level == "A1":
        return parse_a1_wordlist(path)
    return parse_a2_wordlist(path, level=level)


def _parse_topics(paths: dict[str, Path]) -> list[WordRecord]:
    """Parse the thematic WORTGRUPPEN sections (#71) into topic-tagged records: the B1
    numbered section and the A2 front-matter tables. A seam of its own so tests can
    substitute it without a real PDF, as with `_parse_level`."""
    return parse_b1_topics(paths["B1"]) + parse_a2_topics(paths["A2"])


def ingest_goethe(db: DbSession, source_root: Path) -> GoetheStats:
    """Parse the A1/A2/B1 Wortlisten under ``source_root`` and persist them as Goethe
    word nodes, then derive verb-family edges once over the combined set.

    Mirrors the parse -> persist_words(source="goethe") -> derive_family_edges chain
    the lexical-graph tests exercise. Idempotent: persist_words upserts on
    (lemma, pos, level), so re-running updates rows in place rather than duplicating."""
    paths = goethe_pdf_paths(source_root)
    levels: list[LevelStats] = []
    stored_ids: set[int] = set()
    for level, path in paths.items():
        records = _parse_level(level, path)
        persisted = persist_words(db, records, source="goethe")
        # persist_words returns one Word per record; duplicate keys yield the same
        # row twice, so dedupe by id to count what actually landed in the store.
        stored = {w.id: w for w in persisted}
        stored_ids |= set(stored)
        levels.append(
            LevelStats(
                level=level,
                parsed=len(records),
                stored=len(stored),
                needs_review=sum(w.needs_review for w in stored.values()),
            )
        )
    # Thematic topics run after the alphabetical persist so they stamp topic onto rows
    # already present, then insert any thematic-only lemmas. Fold the touched rows into
    # the coverage id-set so total_words counts the inserts once.
    topics, topic_words = apply_topics(db, _parse_topics(paths), source="goethe")
    stored_ids |= {w.id for w in topic_words}
    edges = derive_family_edges(db)
    return GoetheStats(
        levels=levels,
        family_edges=edges,
        topics=topics,
        total_words=len(stored_ids),
    )


def _stats_dict(stats: GoetheStats) -> dict:
    """Shape a run summary for the on-disk stats file, keyed by source so the
    glossary/vision paths can add their own sections later without a rewrite."""
    return {
        "goethe": {
            "levels": {
                s.level: {
                    "parsed": s.parsed,
                    "stored": s.stored,
                    "needs_review": s.needs_review,
                }
                for s in stats.levels
            },
            "topics": {
                "matched": stats.topics.matched,
                "inserted": stats.topics.inserted,
            },
            "family_edges": stats.family_edges,
            "total_words": stats.total_words,
        }
    }


def _write_stats(stats: GoetheStats, data_dir: Path) -> dict | None:
    """Write the run summary to ``data_dir/ingest_stats.json`` and return the previous
    run's dict for a diff, or None on the first run."""
    path = data_dir / "ingest_stats.json"
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_stats_dict(stats), indent=2), encoding="utf-8")
    return previous


def _format_report(stats: GoetheStats, previous: dict | None) -> str:
    prev_levels = ((previous or {}).get("goethe", {})).get("levels", {})
    lines = ["Goethe Wortliste ingest (source=goethe):"]
    for s in stats.levels:
        was = prev_levels.get(s.level, {}).get("stored")
        delta = f" ({s.stored - was:+d})" if was is not None else ""
        merged = (
            f", {s.parsed - s.stored} homographs merged" if s.parsed != s.stored else ""
        )
        lines.append(
            f"  {s.level}: {s.stored} words{delta} (from {s.parsed} parsed{merged}), "
            f"{s.needs_review} flagged needs_review"
        )
    lines.append(
        f"  topics: {stats.topics.matched} tagged, {stats.topics.inserted} inserted"
    )
    lines.append(f"  family edges: {stats.family_edges}")
    lines.append(f"  total: {stats.total_words} distinct words")
    lines.append(
        "Only the Goethe path is wired; glossary/vision/Qdrant are separate issues."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="python -m app.rag.ingest",
        description="Ingest source PDFs into the WortlAI knowledge stores "
        "(Goethe Wortlisten only for now).",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=settings.books_dir,
        help="Root of the source PDF corpus (default: the books_dir setting).",
    )
    args = parser.parse_args(argv)

    with session_scope() as db:
        stats = ingest_goethe(db, args.source)
    previous = _write_stats(stats, settings.data_dir)
    print(_format_report(stats, previous))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
