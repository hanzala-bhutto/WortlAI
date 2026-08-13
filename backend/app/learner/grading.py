"""Turn a conversation-use signal into an FSRS grade.

Grades in WortlAI come from how a chunk was used in a session, never from a
flashcard tap (CLAUDE.md, Pedagogy): used unprompted grades Good (Easy on a
repeated unprompted use), a chunk that needed a gloss grades Hard, one failed or
avoided grades Again. This module is the whole mapping and nothing else - it
takes an already classified `Signal`, so it stays a pure function the grade-map
test pins without a transcript. Classifying a transcript into these signals is
the session-end path's job, not this one.
"""

from enum import StrEnum

from fsrs import Rating

# An unprompted use grades Good; only a confidently repeated one in the same
# session earns Easy, so a single lucky hit does not stretch the interval.
EASY_UNPROMPTED_USES = 2


class Signal(StrEnum):
    """How a chunk showed up in conversation - the input the grade is read from."""

    UNPROMPTED = "unprompted"  # produced correctly with no prompt or gloss
    GLOSSED = "glossed"  # produced, but only after a gloss or hint
    FAILED = "failed"  # attempted and got wrong
    AVOIDED = "avoided"  # expected but sidestepped entirely


def signal_to_rating(signal: Signal, unprompted_uses: int = 1) -> Rating:
    """The one place conversation signal becomes an FSRS Rating. `unprompted_uses`
    only bears on an UNPROMPTED signal (Good vs Easy); other signals ignore it."""
    if signal is Signal.UNPROMPTED:
        return Rating.Easy if unprompted_uses >= EASY_UNPROMPTED_USES else Rating.Good
    if signal is Signal.GLOSSED:
        return Rating.Hard
    if signal in (Signal.FAILED, Signal.AVOIDED):
        return Rating.Again
    raise ValueError(f"unhandled signal: {signal!r}")
