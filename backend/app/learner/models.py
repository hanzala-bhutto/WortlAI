"""The learner store's tables: sessions, error log, immersion hours.

Kept intentionally close to the domain: a Session is one conversation, an
ErrorLog row is one mistake the Corrector caught in it, an ImmersionLog row is a
block of German time from any source. Later phases hang FSRS cards and the
lexical graph off the same Base; nothing here presumes them.

Enumerated columns (severity, source) are plain strings validated at the API
boundary, so widening the taxonomy is an app change, not a migration.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.learner.db import Base

# Vocabulary these string columns carry, enforced at the boundary that writes
# them (the /log-hours Literal today, the Corrector's schema in #5), not here -
# plain strings keep the taxonomy wide-able without a migration:
#   ImmersionLog.source: "app" | "call" | "mission"
#   ErrorLog.severity:   "critical" | "minor"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Session(Base):
    """One conversation. Open while it runs (ended_at is null), closed when it
    finishes. Duration is derived from the two timestamps, not stored - one fact,
    one place, no drift."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(default=None)
    scenario: Mapped[str | None] = mapped_column(String(120), default=None)

    errors: Mapped[list["ErrorLog"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )
    immersion: Mapped[list["ImmersionLog"]] = relationship(
        back_populates="session",
        # App-sourced hours belong to a session; deleting the session takes them
        # with it. Call/mission rows have no session and are untouched.
        cascade="all, delete-orphan",
    )

    def mark_ended(self, when: datetime | None = None) -> None:
        """Close the session by stamping when it finished."""
        self.ended_at = when or _utcnow()

    @property
    def duration_seconds(self) -> int | None:
        """How long the conversation lasted, or None while it is still open.
        Derived on read so it can never disagree with the timestamps."""
        if self.ended_at is None:
            return None
        started, ended = self.started_at, self.ended_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        if ended.tzinfo is None:
            ended = ended.replace(tzinfo=UTC)
        return max(0, int((ended - started).total_seconds()))


class ErrorLog(Base):
    """One mistake caught in a session: what was said, the fix, and how much it
    mattered. The correction is what a card or a debrief item is built from."""

    __tablename__ = "error_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    # A dotted taxonomy path, e.g. "grammar.case.dative" or "vocab.false-friend".
    error_type: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    utterance: Mapped[str] = mapped_column(Text)
    correction: Mapped[str] = mapped_column(Text)
    # A short learner-facing note on why it was wrong, shown in the debrief. Nullable
    # so an error caught before the Corrector wrote explanations (or by a hand tool)
    # still stores.
    explanation: Mapped[str | None] = mapped_column(Text, default=None)

    session: Mapped[Session] = relationship(back_populates="errors")


# The five edge types the lexical graph carries and the two provenances an edge
# can have. Strings validated at the write boundary (rag/lexical_graph.py), not by
# an enum column, so widening the taxonomy stays an app change - the same
# plain-string discipline the error/immersion columns above use.
#   WordLink.edge_type: IN_FAMILY | COLLOCATES_WITH | GOVERNS | SYNONYM | ANTONYM
#   WordLink.source:    deterministic | llm
#   Word.source:        glossary | goethe | vision


class Word(Base):
    """One lexical node: a lemma the learner can be taught, with the structural
    facts #9's glossary parse produced. The graph's edges (word_links) point at
    these rows; #13 (Goethe coverage) and #14 (FSRS word_states) hang off the
    same table. Structural fields are copied from the deterministic parse, never
    from an LLM - only relational edges are model-extracted, and those are cited."""

    __tablename__ = "words"
    # A lemma can recur across levels or parts of speech (das Recht / recht /
    # richten), so identity is the triple, not the lemma alone; a re-ingest of the
    # same glossary updates in place instead of duplicating.
    __table_args__ = (UniqueConstraint("lemma", "pos", "level"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lemma: Mapped[str] = mapped_column(String(120), index=True)
    # The verbatim glossary string the lemma was read from, kept for audit exactly
    # as #9's WordRecord.lemma_raw does - the citation of last resort for a node.
    lemma_raw: Mapped[str] = mapped_column(String(240))
    pos: Mapped[str] = mapped_column(String(16))  # noun | verb | other
    article: Mapped[str | None] = mapped_column(String(8), default=None)
    plural: Mapped[str | None] = mapped_column(String(80), default=None)
    # VerbForm flattened onto the row (rather than a JSON blob) so the family
    # derivation can query prefix/infinitive directly in SQL.
    verb_prefix: Mapped[str | None] = mapped_column(String(40), default=None)
    verb_infinitive: Mapped[str | None] = mapped_column(String(80), default=None)
    verb_aux: Mapped[str | None] = mapped_column(String(8), default=None)  # haben|sein
    # One retained example sentence, when the source carried one. The A1 glossary
    # mostly does not (docs/feasibility/012); #10's Redemittel/grammar text is the
    # richer citation corpus. Nullable so a bare word list still stores.
    example_de: Mapped[str | None] = mapped_column(Text, default=None)
    translation_en: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(8), index=True)
    chapter: Mapped[int | None] = mapped_column(Integer, default=None)
    chapter_title: Mapped[str | None] = mapped_column(String(200), default=None)
    topic: Mapped[str | None] = mapped_column(String(200), default=None, index=True)
    source: Mapped[str] = mapped_column(
        String(16), index=True
    )  # glossary|goethe|vision
    source_page: Mapped[int | None] = mapped_column(Integer, default=None)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    outgoing: Mapped[list["WordLink"]] = relationship(
        back_populates="from_word",
        foreign_keys="WordLink.from_word_id",
        cascade="all, delete-orphan",
    )


class WordLink(Base):
    """A typed, directed edge between two words. `source` records how the edge was
    made: `deterministic` edges come from the glossary parse (family membership)
    and need no citation - the rule is the provenance; `llm` edges are
    model-extracted and MUST quote a real corpus span (guardrail 5). The CHECK
    below makes an uncited LLM edge unwritable at the storage layer, so
    /graph-check's uncited-edge audit returns zero by construction, not by
    discipline."""

    __tablename__ = "word_links"
    __table_args__ = (
        # One edge of a given type between an ordered pair; a re-ingest re-validates
        # rather than piling up duplicates.
        UniqueConstraint("from_word_id", "to_word_id", "edge_type"),
        CheckConstraint(
            "source != 'llm' OR (citation IS NOT NULL AND citation != '')",
            name="llm_edge_cited",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    from_word_id: Mapped[int] = mapped_column(
        ForeignKey("words.id", ondelete="CASCADE"), index=True
    )
    to_word_id: Mapped[int] = mapped_column(
        ForeignKey("words.id", ondelete="CASCADE"), index=True
    )
    edge_type: Mapped[str] = mapped_column(String(24), index=True)
    source: Mapped[str] = mapped_column(String(16))  # deterministic | llm
    # The corpus span an LLM edge quotes. NULL is allowed only for deterministic
    # edges (enforced by the CHECK above); for GOVERNS this is the grammar-box
    # span the government was read from.
    citation: Mapped[str | None] = mapped_column(Text, default=None)
    # Structured edge detail. For GOVERNS this carries the prep+case marker
    # (e.g. "auf+Akk") /graph-check check 5 requires; other edge types leave it
    # NULL. Kept separate from citation so the marker stays machine-checkable.
    detail: Mapped[str | None] = mapped_column(String(40), default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    from_word: Mapped[Word] = relationship(
        back_populates="outgoing", foreign_keys=[from_word_id]
    )
    to_word: Mapped[Word] = relationship(foreign_keys=[to_word_id])


class ErrorPatternLink(Base):
    """Links an error pattern to a word it touches. There is no ErrorPattern
    entity in this codebase - a pattern is an `ErrorLog.error_type` dotted string
    aggregated (queries.error_counts_by_type) - so this keys on that same string,
    not a foreign key. The table and its writer land in #12; population is Phase 3
    (Corrector/Curriculum, #16/#26), which is why nothing here presumes a row."""

    __tablename__ = "error_pattern_links"
    __table_args__ = (UniqueConstraint("error_type", "word_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    error_type: Mapped[str] = mapped_column(String(80), index=True)
    word_id: Mapped[int] = mapped_column(
        ForeignKey("words.id", ondelete="CASCADE"), index=True
    )
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    word: Mapped[Word] = relationship()


class ImmersionLog(Base):
    """A block of German time. app-sourced rows link to the session that produced
    them; call and mission rows are logged by hand and stand alone."""

    __tablename__ = "immersion_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    source: Mapped[str] = mapped_column(String(16), index=True)
    minutes: Mapped[int] = mapped_column()
    note: Mapped[str | None] = mapped_column(String(280), default=None)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), default=None, index=True
    )

    session: Mapped[Session | None] = relationship(back_populates="immersion")
