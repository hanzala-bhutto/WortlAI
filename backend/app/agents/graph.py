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
sequence resumed from the checkpoint. correct runs after converse every turn; in
v1 it is a validated no-op seam that #5 (the Corrector) fills.

Nodes emit their reply to the graph's stream writer, so a caller using
`astream(stream_mode="custom")` gets the reply in chunks; under a plain `ainvoke`
the writer is a harmless no-op.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.agents.persistence import SessionWriter
from app.agents.scenarios import get_scenario
from app.agents.state import SessionState
from app.agents.tutor import Tutor, iter_chunks


@dataclass
class SessionGraphDeps:
    """What the graph nodes need injected: the Tutor that speaks and the writer
    that persists the session. Passed in so a test can supply fakes/temp DBs."""

    tutor: Tutor
    persister: SessionWriter


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
        return {"messages": messages, "user_input": None}

    async def correct(_state: SessionState) -> dict:
        # Seam for the Corrector (#5): inspect the last user turn and append
        # validated error rows to pending_errors. No-op in v1, but wired into the
        # graph now so #5 is a body change, not a topology change.
        return {}

    async def debrief(state: SessionState) -> dict:
        session_id = state.get("session_id")
        if session_id is not None:
            errors = state.get("pending_errors", [])
            await asyncio.to_thread(
                persister.end_session, session_id, errors=errors
            )
        return {"phase": "done"}

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
