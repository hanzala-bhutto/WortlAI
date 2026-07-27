"""The graph's state: everything one conversation carries between turns.

Persisted by the checkpointer (its own checkpoints.db), so this is what survives a
backend restart. Kept to plain JSON-friendly types - dicts, strings, ints - so the
serializer never has to reach for anything exotic.

The channels use LangGraph's default last-value semantics, so every node returns
the *full* value for any key it changes (e.g. the whole `messages` list, not a
delta). That keeps the reducer trivial and the persisted state unambiguous.
"""

from __future__ import annotations

from typing import TypedDict

from app.llmops.prompts import ChatMessage


class SessionState(TypedDict, total=False):
    # Set once by the setup node when the learner-store Session row is created;
    # error_logs and immersion hours are written against it at debrief.
    session_id: int
    scenario_id: str  # which roleplay; resolves to a Scenario in the registry
    level: str  # the CEFR rung the Tutor is pinned to for this session

    # The conversation so far, user/assistant turns only. The system prompt is
    # fetched fresh each turn from the prompt store and is never stored here.
    messages: list[ChatMessage]

    # The new user utterance for this turn, consumed by the converse node and
    # cleared. Passed as input per invocation; the history above comes from the
    # checkpoint, so a caller only ever sends the latest turn.
    user_input: str | None

    # Errors the Corrector (#5) attaches during the turn; written to error_logs at
    # debrief. Empty in v1 - the correct node is a seam until #5 fills it.
    pending_errors: list[dict]

    # Flips the entry router to the debrief node so a session can be closed out.
    end_requested: bool

    # Where the session is: "converse" while running, "done" after debrief. Purely
    # informational - routing keys off session_id and end_requested, not this.
    phase: str
