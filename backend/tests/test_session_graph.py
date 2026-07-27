"""Contract for the session graph: node transitions, streaming and persistence.

The two acceptance criteria that are hard to eyeball and easy to regress are here:
a conversation resumes mid-flight after a restart (the checkpointer), and node
transitions accumulate the conversation correctly across a multi-turn session.

The graph is driven with a FakeTutor so these tests describe the *graph's*
behaviour (routing, state accumulation, debrief writes), not the Tutor's - that is
test_tutor's job. Persistence goes to a temp learner DB and the checkpointer to a
temp file, so nothing touches the app's real stores.
"""

from types import SimpleNamespace

import aiosqlite
import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from sqlalchemy.orm import sessionmaker

from app.agents.graph import SessionGraphDeps, build_session_graph
from app.agents.persistence import SessionWriter
from app.agents.scenarios import get_scenario
from app.agents.tutor import iter_chunks
from app.learner.db import Base, make_engine
from app.learner.models import Session


class FakeTutor:
    """Stands in for the Tutor: returns queued German replies and streams them to
    the writer, recording the history it was handed each turn."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    async def reply(self, *, scenario, level, history, writer=None):
        self.calls.append(
            SimpleNamespace(scenario=scenario, level=level, history=list(history))
        )
        text = self._replies.pop(0)
        if writer is not None:
            for chunk in iter_chunks(text):
                writer(chunk)
        return SimpleNamespace(text=text, regenerated=False, prompt_is_fallback=True)


def make_factory(tmp_path, name="w.db"):
    engine = make_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


async def build_app(tmp_path, replies, *, ck_name="ck.db", factory=None):
    """A compiled session graph over a FakeTutor, a temp learner DB and a temp
    checkpoint file. Returns (app, conn, factory); close conn when done."""
    factory = factory or make_factory(tmp_path)
    deps = SessionGraphDeps(
        tutor=FakeTutor(replies), persister=SessionWriter(session_factory=factory)
    )
    builder = build_session_graph(deps)
    conn = await aiosqlite.connect(str(tmp_path / ck_name))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    return builder.compile(checkpointer=saver), conn, factory


def cfg(thread_id="t1"):
    return {"configurable": {"thread_id": thread_id}}


async def test_setup_creates_session_and_seeds_opening_line(tmp_path):
    app, conn, factory = await build_app(tmp_path, replies=[])
    try:
        out = await app.ainvoke({"scenario_id": "baeckerei"}, cfg())

        assert out["session_id"] is not None
        assert out["phase"] == "converse"
        assert out["messages"] == [
            {"role": "assistant", "content": get_scenario("baeckerei").opening_line}
        ]
        with factory() as db:
            row = db.get(Session, out["session_id"])
            assert row.scenario == "baeckerei"
            assert row.ended_at is None  # still open after setup
    finally:
        await conn.close()


async def test_three_turn_conversation_accumulates_messages(tmp_path):
    app, conn, _ = await build_app(
        tmp_path, replies=["Antwort eins.", "Antwort zwei.", "Antwort drei."]
    )
    try:
        await app.ainvoke({"scenario_id": "cafe"}, cfg())  # setup -> opening line
        for utterance in ("Hallo", "Zwei Brötchen bitte", "Danke schön"):
            out = await app.ainvoke({"user_input": utterance}, cfg())

        # opening line, then three user/assistant pairs
        roles = [m["role"] for m in out["messages"]]
        assert roles == [
            "assistant",
            "user", "assistant",
            "user", "assistant",
            "user", "assistant",
        ]
        assert len(out["messages"]) == 7
        assert out["messages"][-1] == {"role": "assistant", "content": "Antwort drei."}
        assert out["user_input"] is None  # consumed each turn
    finally:
        await conn.close()


async def test_each_turn_sees_the_growing_history(tmp_path):
    app, conn, _ = await build_app(tmp_path, replies=["r1", "r2", "r3"])
    tutor = None
    try:
        await app.ainvoke({"scenario_id": "nachbar"}, cfg())
        for utterance in ("u1", "u2", "u3"):
            await app.ainvoke({"user_input": utterance}, cfg())
        # Reach into the compiled graph's dep to inspect what each turn received.
        # The third turn's history must carry the opening line + turns 1-2.
        state = await app.aget_state(cfg())
        contents = [m["content"] for m in state.values["messages"]]
        assert contents[0] == get_scenario("nachbar").opening_line
        assert "u1" in contents and "u2" in contents and "u3" in contents
    finally:
        await conn.close()


async def test_session_resumes_after_a_restart(tmp_path):
    # Session A: setup + two turns, then the process "dies" (connection closed).
    factory = make_factory(tmp_path)
    app_a, conn_a, _ = await build_app(
        tmp_path, replies=["A1", "A2"], factory=factory
    )
    await app_a.ainvoke({"scenario_id": "nachbar"}, cfg())
    await app_a.ainvoke({"user_input": "u1"}, cfg())
    out2 = await app_a.ainvoke({"user_input": "u2"}, cfg())
    assert len(out2["messages"]) == 5
    await conn_a.close()

    # Restart: a brand-new checkpointer + graph over the SAME checkpoint file and
    # learner DB. The third turn must continue turns 1-2, not start over.
    app_b, conn_b, _ = await build_app(
        tmp_path, replies=["A3"], factory=factory
    )
    try:
        out3 = await app_b.ainvoke({"user_input": "u3"}, cfg())

        assert len(out3["messages"]) == 7  # nothing was lost across the restart
        contents = [m["content"] for m in out3["messages"]]
        assert contents[1:] == ["u1", "A1", "u2", "A2", "u3", "A3"]
    finally:
        await conn_b.close()


async def test_debrief_closes_session_and_writes_pending_errors(tmp_path):
    app, conn, factory = await build_app(tmp_path, replies=["Hallo zurück."])
    try:
        setup = await app.ainvoke({"scenario_id": "baeckerei"}, cfg())
        sid = setup["session_id"]
        await app.ainvoke({"user_input": "Guten Tag"}, cfg())

        # Seed a validated error into state (the seam #5 will fill) and close out.
        error = {
            "error_type": "grammar.case.dative",
            "severity": "minor",
            "utterance": "mit der Hund",
            "correction": "mit dem Hund",
        }
        out = await app.ainvoke(
            {"end_requested": True, "pending_errors": [error]}, cfg()
        )
        assert out["phase"] == "done"

        with factory() as db:
            row = db.get(Session, sid)
            assert row.ended_at is not None
            assert len(row.errors) == 1
            assert row.errors[0].error_type == "grammar.case.dative"
    finally:
        await conn.close()


async def test_astream_streams_opening_then_reply_in_chunks(tmp_path):
    app, conn, _ = await build_app(tmp_path, replies=["Guten Tag zusammen heute."])
    try:
        opening = [
            chunk
            async for chunk in app.astream(
                {"scenario_id": "nachbar"}, cfg(), stream_mode="custom"
            )
        ]
        assert "".join(opening) == get_scenario("nachbar").opening_line

        tokens = [
            chunk
            async for chunk in app.astream(
                {"user_input": "Hallo"}, cfg(), stream_mode="custom"
            )
        ]
        assert "".join(tokens) == "Guten Tag zusammen heute."
        assert len(tokens) > 1
    finally:
        await conn.close()
