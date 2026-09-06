# 078 - Due-queue REST endpoint over due_words

Expose #14's `queries.due_words` at `GET /api/v1/reviews/due` so the review screen
and the `/progress` skill can read what is due without importing the learner package.
Everything the endpoint returns already exists on the read side; this is a thin,
validated HTTP surface over a query that is merged and tested, not new scheduling
logic.

## What already exists

`queries.due_words(db, now=None, limit=None) -> list[WordState]`
(`app/learner/queries.py:66`) is the review queue: `WordState.due <= now`, ordered by
`due` ascending (soonest-first), optional `limit`. That is exactly the endpoint's
contract, so the acceptance line "excludes cards scheduled into the future (mirrors
`due <= now`)" is satisfied by the query itself - the endpoint adds no `where` clause
of its own and must not, or the two definitions of "due" could drift.

`WordState` carries `due` and `state` and a `word` relationship back to `Word`
(`models.py:268`); `Word` has `lemma` and `level`. So the join the AC asks for
("each joined to its `Word` (lemma, level)") is a relationship access, not a hand-
written join.

## The one design call: new `reviews.py` router vs. bolt onto `learner.py`

**Chosen: a new `app/api/v1/reviews.py` router.** The v1 aggregator docstring already
names "review" as an expected router alongside session/voice/ingest/progress
(`app/api/v1/__init__.py:3`), and the review queue is a distinct resource from manual
immersion logging and session debriefs (what `learner.py` owns). Keeping it separate
means `/reviews/*` grows (grade submission, queue counts) without swelling the learner
module. Cost is one `include_router` line. Rejected: appending to `learner.py` - saves
the file but muddies two unrelated concerns and contradicts the aggregator's own map.

## Response shape

A flat row per due card, not a nested `Word` object - the two consumers (review UI,
`/progress`) want lemma + level + timing inline, and flattening keeps the JSON small
and the `response_model` obvious:

```
DueCard: word_id:int, lemma:str, level:str, due:datetime, state:int
```

`response_model=list[DueCard]` (no `-> dict`, per CLAUDE.md). Built with
`ConfigDict(from_attributes=True)` off `WordState` plus its `.word`, matching the
`ImmersionLogEntry`/`SessionDebrief` pattern already in `learner.py`. An empty queue
returns `[]` - a `list` response_model over an empty list, never a 404. `limit` is an
optional query param (`Query(default=None, gt=0)`) passed straight through to
`due_words`; a non-positive limit is a 422 from validation, not a silent full scan.

`state` is included because the UI distinguishes new/learning from review cards; it is
already a mirrored int column, free to return.

## Tests (written first)

- Seeded queue: three `WordState` rows, two due (past `due`), one future - endpoint
  returns exactly the two, soonest-first, each with the right lemma/level.
- Empty queue returns `200` and `[]`, not an error.
- `limit=1` returns only the soonest of several due cards.
- `limit=0` / negative is a `422` (validation), not a full read.
- Future-only queue returns `[]` (the `due <= now` boundary holds through the endpoint).

## Effort

S. One router module, one `include_router` line, one flat Pydantic model, no schema
change and no new query - `due_words` already does the work. No new deps.

## Verdict

**GO.** The dependency (#14) is merged and its query is the exact contract; the only
judgement call - a dedicated `reviews.py` router - is the one the aggregator already
anticipates.
