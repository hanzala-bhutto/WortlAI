"""Review-queue endpoints. The read side of FSRS: what is due right now.

The Talk/Review UI and the /progress skill both need "what should be resurfaced",
and neither should import the learner package to get it. This exposes #14's
queries.due_words over HTTP - due cards soonest-first, each flattened with its Word's
lemma and level - and nothing more; grading a card is a separate concern with its own
endpoint when it lands.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.learner.db import get_db
from app.learner.queries import due_words

router = APIRouter(tags=["reviews"])


class DueCard(BaseModel):
    word_id: int = Field(description="The Word this card schedules.")
    lemma: str = Field(description="The lemma to review, e.g. 'ankommen'.")
    level: str = Field(description="CEFR level the word was ingested at, e.g. 'A2'.")
    due: datetime = Field(
        description="When the card came due (UTC). Past for a due card."
    )
    state: int = Field(description="FSRS stage: 1 Learning, 2 Review, 3 Relearning.")


@router.get(
    "/reviews/due",
    response_model=list[DueCard],
    summary="The review queue: cards due now, soonest-due first",
)
def get_due_reviews(
    limit: int | None = Query(
        default=None, gt=0, description="Cap the queue length. Omit for all due cards."
    ),
    db: DbSession = Depends(get_db),
) -> list[DueCard]:
    # due_words owns the `due <= now` boundary and the ordering - the endpoint adds no
    # filter of its own, so the UI's notion of "due" can't drift from the engine's.
    return [
        DueCard(
            word_id=state.word_id,
            lemma=state.word.lemma,
            level=state.word.level,
            due=state.due,
            state=state.state,
        )
        for state in due_words(db, limit=limit)
    ]
