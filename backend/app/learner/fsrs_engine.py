"""py-fsrs, quarantined behind one interface.

Nothing outside this module imports `fsrs`. It turns a stored card (the JSON blob
in word_states.card_json) plus a Rating and a review time into the next card blob
and its due date, so swapping the library version or the scheduler config is a
change here and nowhere else - the same discipline llm/provider.py and
voice/tts.py apply to their vendors. py-fsrs is UTC-only; callers pass tz-aware
UTC datetimes in and get them back.
"""

import json
from dataclasses import dataclass
from datetime import datetime

from fsrs import Card, Rating, Scheduler

# Default FSRS parameters. The optimizer that would tune these to Hanzala's own
# review history waits for 2-3 months of data (CLAUDE.md), so stock weights now;
# the review_events log is what that optimizer will eventually read.
_scheduler = Scheduler()


@dataclass(frozen=True)
class ReviewResult:
    """One review's outcome: the blob to persist, plus the two fields word_states
    mirrors into indexed columns so the due queue is a plain SQL read."""

    card_json: str
    due: datetime
    state: int


def review(card_json: str | None, rating: Rating, when: datetime) -> ReviewResult:
    """Advance a card by one review at `when`. `card_json` is None the first time a
    word is graded - a fresh card is created and is due immediately; any later call
    round-trips the stored blob, so no FSRS internal is ever reconstructed by
    hand."""
    if when.tzinfo is None:
        raise ValueError("review time must be timezone-aware UTC")
    card = Card() if card_json is None else Card.from_dict(json.loads(card_json))
    card, _log = _scheduler.review_card(card, rating, review_datetime=when)
    return ReviewResult(
        card_json=json.dumps(card.to_dict()),
        due=card.due,
        state=card.state.value,
    )
