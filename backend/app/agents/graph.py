"""The session as a checkpointed LangGraph state graph.

Shape (feasibility 004): setup -> converse -> correct -> debrief. The graph is
compiled once and driven one invocation per user turn; the checkpointer carries
`messages` between turns, so a caller only ever sends the latest utterance and the
conversation survives a restart.

Per-invocation routing (the entry router keys off state, not a fixed start node):

    START ─► setup      (first invocation: no session_id yet)   ─► END
          ─► converse ─► correct   (each user turn)             ─► END
          ─► debrief    (end_requested: close the session)      ─► END

So the "converse ⇄ correct" loop is external: each turn is its own super-step
sequence resumed from the checkpoint. correct runs after converse every turn and
fire-and-forgets the Corrector on the utterance (#5); the analysis is gathered and
persisted at debrief, so it never adds latency to a turn.

Nodes emit their reply to the graph's stream writer, so a caller using
`astream(stream_mode="custom")` gets the reply in chunks; under a plain `ainvoke`
the writer is a harmless no-op.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.agents.corrector import CorrectorCollector
from app.agents.persistence import SessionWriter
from app.agents.scenarios import get_scenario
from app.agents.state import SessionState
from app.agents.tutor import Tutor, iter_chunks


@dataclass
class SessionGraphDeps:
    """What the graph nodes need injected: the Tutor that speaks, the collector that
    runs the (async) Corrector off the critical path, and the writer that persists
    the session. Passed in so a test can supply fakes/temp DBs."""

    tutor: Tutor
    collector: CorrectorCollector
    persister: SessionWriter


def _reply_context(messages: list) -> str | None:
    """The assistant line the latest user turn was responding to: the last assistant
    message before the most recent user message. Gives the Corrector the exchange the
    utterance belongs to, so it isn't judging the sentence in a vacuum."""
    last_user = next(
        (
            i
            for i in range(len(messages) - 1, -1, -1)
            if messages[i].get("role") == "user"
        ),
        None,
    )
    if last_user is None:
        return None
    return next(
        (
            messages[j]["content"]
            for j in range(last_user - 1, -1, -1)
            if messages[j].get("role") == "assistant"
        ),
        None,
    )


def _route_entry(state: SessionState) -> str:
    """Pick this invocation's entry node from the checkpointed state: close out on
    request, otherwise set up a brand-new session, otherwise take a normal turn."""
    if state.get("end_requested"):
        return "debrief"
    if state.get("session_id") is None:
        return "setup"
    return "converse"


def build_session_graph(deps: SessionGraphDeps) -> StateGraph:
    """The uncompiled session graph over `deps`. Compile it with a checkpointer
    (see checkpointer.open_checkpointer) to get a runnable that persists state."""
    tutor = deps.tutor
    collector = deps.collector
    persister = deps.persister

    async def setup(state: SessionState) -> dict:
        writer = get_stream_writer()
        scenario = get_scenario(state["scenario_id"])
        level = state.get("level") or scenario.level
        # Open the learner-store row now so errors and the debrief can reference it.
        session_id = await asyncio.to_thread(persister.create_session, scenario.id)
        # The opening line is fixed scenario data (deterministic, already German and
        # in persona), so no model call - just stream it out as the first turn.
        for chunk in iter_chunks(scenario.opening_line):
            writer(chunk)
        return {
            "session_id": session_id,
            "scenario_id": scenario.id,
            "level": level,
            "messages": [{"role": "assistant", "content": scenario.opening_line}],
            "pending_errors": [],
            "phase": "converse",
        }

    async def converse(state: SessionState) -> dict:
        writer = get_stream_writer()
        scenario = get_scenario(state["scenario_id"])
        level = state.get("level") or scenario.level
        messages = list(state.get("messages", []))
        user_input = state.get("user_input")
        if user_input:
            messages.append({"role": "user", "content": user_input})
        reply = await tutor.reply(
            scenario=scenario, level=level, history=messages, writer=writer
        )
        messages.append({"role": "assistant", "content": reply.text})
        # user_input is left in state for the correct node to consume: it is the
        # signal that *this* turn carried a new utterance to analyse.
        return {"messages": messages}

    async def correct(state: SessionState) -> dict:
        # Fire-and-forget the Corrector on this turn's user utterance and return at
        # once, so error analysis never adds latency to the turn or the next one. The
        # spawned task is gathered at debrief; nothing is persisted to state here.
        # Gate on user_input (not the message history) so an invocation that carried
        # no new utterance can't re-submit a prior turn and double-count its errors.
        session_id = state.get("session_id")
        utterance = state.get("user_input")
        if session_id is not None and utterance:
            context = _reply_context(state.get("messages", []))
            collector.submit(session_id, utterance=utterance, context=context)
        # Consume the utterance now that it has been dispatched, so it is analysed
        # exactly once.
        return {"user_input": None}

    async def debrief(state: SessionState) -> dict:
        session_id = state.get("session_id")
        if session_id is None:
            return {"phase": "done"}
        # Gather every analysis the turns spawned (severity-filtered), fold in any
        # errors already seeded into state, and persist the lot as validated rows.
        caught = await collector.collect(session_id)
        errors = [*state.get("pending_errors", []), *caught]
        await asyncio.to_thread(persister.end_session, session_id, errors=errors)
        return {"phase": "done", "pending_errors": errors}

    builder = StateGraph(SessionState)
    builder.add_node("setup", setup)
    builder.add_node("converse", converse)
    builder.add_node("correct", correct)
    builder.add_node("debrief", debrief)

    builder.add_conditional_edges(
        START,
        _route_entry,
        {"setup": "setup", "converse": "converse", "debrief": "debrief"},
    )
    builder.add_edge("setup", END)
    builder.add_edge("converse", "correct")
    builder.add_edge("correct", END)
    builder.add_edge("debrief", END)
    return builder
