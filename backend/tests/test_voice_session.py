"""Contract for the voice loop (run_voice_session).

Driven against the REAL session graph (over a FakeTutor, a temp learner DB and a
temp checkpoint file) and a fake WebSocket, all in one event loop - so the whole
protocol round-trip is proven end to end without a socket, a browser, or the
network. STT and TTS are fakes; their own contracts live in test_voice_stt / _tts.

What matters and would silently regress: the start -> turn -> end round-trip emits
the right frames in order; the Tutor reply is spoken a sentence at a time (audio
frame per sentence, not one blob at the end); every degraded step (audio before
start, STT failure, silence, turn cap) is a reported, non-fatal frame; and a
reconnect on the same thread_id resumes instead of restarting.
"""

import base64
import json
from types import SimpleNamespace

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from sqlalchemy.orm import sessionmaker

from app.agents.corrector import CorrectorCollector
from app.agents.graph import SessionGraphDeps, build_session_graph
from app.agents.persistence import SessionWriter
from app.agents.scenarios import get_scenario
from app.agents.tutor import iter_chunks
from app.learner.db import Base, make_engine
from app.learner.models import Session
from app.voice.session import _cfg, run_voice_session
from app.voice.stt import STTError


class FakeTutor:
    def __init__(self, replies):
        self._replies = list(replies)

    async def reply(self, *, scenario, level, history, writer=None):
        text = self._replies.pop(0)
        if writer is not None:
            for chunk in iter_chunks(text):
                writer(chunk)
        return SimpleNamespace(text=text, regenerated=False, prompt_is_fallback=True)


class FakeTranscriber:
    def __init__(self, transcripts):
        self._q = list(transcripts)
        self.prompts = []

    async def transcribe(self, audio, *, mimetype="audio/webm", prompt=None):
        self.prompts.append(prompt)
        return self._q.pop(0)


class FakeSynth:
    """One audio frame per sentence, tagged with the sentence text so a test can see
    exactly which sentence was spoken and in what order."""

    async def synth(self, text, *, rate=1.0):
        yield b"AUDIO<" + text.encode("utf-8") + b">"


class FakeWS:
    def __init__(self, inbox):
        self._inbox = list(inbox)
        self.sent = []

    async def receive(self):
        if self._inbox:
            return self._inbox.pop(0)
        return {"type": "websocket.disconnect"}

    async def send_json(self, data):
        self.sent.append(data)


def _settings(**over):
    base = {
        "voice_max_turns": 60,
        "voice_rate_default": 1.0,
        "langfuse_user_id": "hanzala",
    }
    base.update(over)
    return SimpleNamespace(**base)


class _NoErrorCorrector:
    """A Corrector that finds nothing, so these voice-transport tests aren't coupled
    to error analysis - that behaviour lives in test_corrector / test_session_graph."""

    async def analyze(self, *, utterance, context=None):
        return []


async def build_graph(tmp_path, replies):
    engine = make_engine(f"sqlite:///{tmp_path / 'w.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    deps = SessionGraphDeps(
        tutor=FakeTutor(replies),
        collector=CorrectorCollector(_NoErrorCorrector()),
        persister=SessionWriter(session_factory=factory),
    )
    conn = await aiosqlite.connect(str(tmp_path / "ck.db"))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    graph = build_session_graph(deps).compile(checkpointer=saver)
    return graph, conn, factory


def up_text(payload):
    return {"type": "websocket.receive", "text": json.dumps(payload)}


def up_bytes(blob):
    return {"type": "websocket.receive", "bytes": blob}


def _types(ws):
    return [m["type"] for m in ws.sent]


async def test_round_trip_start_turn_end(tmp_path):
    graph, conn, factory = await build_graph(
        tmp_path, replies=["Ich habe frische Brötchen. Was möchten Sie?"]
    )
    try:
        ws = FakeWS(
            [
                up_text({"type": "start", "scenario_id": "cafe", "thread_id": "t1"}),
                up_bytes(b"opus-1"),
                up_text({"type": "end"}),
            ]
        )
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=FakeTranscriber(["Einen Kaffee bitte"]),
            synthesizer=FakeSynth(),
            settings=_settings(),
        )

        types = _types(ws)
        assert types[0] == "ready"
        assert types[-1] == "session_closed"
        # The debrief endpoint needs this to fetch the right session.
        assert isinstance(ws.sent[-1]["session_id"], int)

        # The opening line is streamed as reply_token chunks before the first
        # turn_done and reconstructs exactly.
        first_done = types.index("turn_done")
        opening = "".join(
            m["text"] for m in ws.sent[:first_done] if m["type"] == "reply_token"
        )
        assert opening == get_scenario("cafe").opening_line

        # The user's transcript is echoed back.
        assert {
            "type": "transcript",
            "role": "user",
            "text": "Einen Kaffee bitte",
        } in ws.sent

        # The reply's two sentences are spoken as two separate audio frames, in order,
        # not one blob after the whole reply.
        after_transcript = ws.sent[types.index("transcript") :]
        reply_audio = [m for m in after_transcript if m["type"] == "audio"]
        assert len(reply_audio) == 2
        spoken = [base64.b64decode(m["data"]).decode("utf-8") for m in reply_audio]
        assert spoken == [
            "AUDIO<Ich habe frische Brötchen.>",
            "AUDIO<Was möchten Sie?>",
        ]

        # The session was actually closed in the learner store on end.
        with factory() as db:
            rows = db.query(Session).all()
            assert len(rows) == 1 and rows[0].ended_at is not None
    finally:
        await conn.close()


async def test_turn_transcribes_with_the_active_scenarios_redemittel_as_a_prompt(
    tmp_path,
):
    graph, conn, _ = await build_graph(tmp_path, replies=["Bitte schön."])
    try:
        ws = FakeWS(
            [
                up_text({"type": "start", "scenario_id": "cafe", "thread_id": "t1"}),
                up_bytes(b"opus-1"),
            ]
        )
        transcriber = FakeTranscriber(["Einen Kaffee bitte"])
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=transcriber,
            synthesizer=FakeSynth(),
            settings=_settings(),
        )

        # Includes the opening line: it's the Tutor's first reply, streamed via
        # _drive_turn's `setup` call before this first audio turn arrives.
        scenario = get_scenario("cafe")
        assert transcriber.prompts == [
            ", ".join(scenario.redemittel) + " " + scenario.opening_line
        ]
    finally:
        await conn.close()


async def test_second_turn_biases_with_the_tutors_previous_reply(tmp_path):
    graph, conn, _ = await build_graph(
        tmp_path,
        replies=[
            "Ein Apfelsaft, bitte. Wie viele Flaschen möchten Sie?",
            "Vier Flaschen, kommt sofort.",
        ],
    )
    try:
        ws = FakeWS(
            [
                up_text({"type": "start", "scenario_id": "cafe", "thread_id": "t1"}),
                up_bytes(b"opus-1"),
                up_bytes(b"opus-2"),
            ]
        )
        transcriber = FakeTranscriber(["Ich möchte eine Apfelsaft", "Vier bitte"])
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=transcriber,
            synthesizer=FakeSynth(),
            settings=_settings(),
        )

        # The second turn's STT prompt carries the Tutor's reply to the first
        # turn - the vocabulary (Apfelsaft, Flaschen) a hesitant learner is
        # about to echo back and that the static Redemittel never lists.
        assert "Apfelsaft" in transcriber.prompts[1]
        assert "Flaschen" in transcriber.prompts[1]
    finally:
        await conn.close()


class RecordingTracing:
    """Stands in for Tracing (#51/#55): records every session_id/user_id the loop
    scopes a turn with, and every flush, instead of touching real Langfuse/OTEL
    machinery."""

    def __init__(self, *, enabled=True):
        self.calls = []
        self.flush_count = 0
        self.enabled = enabled

    def session(self, *, session_id, user_id=None):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            self.calls.append((session_id, user_id))
            yield

        return _cm()

    def flush(self):
        self.flush_count += 1


async def test_every_turn_is_scoped_to_the_thread_id_for_tracing(tmp_path):
    graph, conn, _ = await build_graph(tmp_path, replies=["Guten Tag!", "Bitte schön."])
    try:
        ws = FakeWS(
            [
                up_text({"type": "start", "scenario_id": "cafe", "thread_id": "t1"}),
                up_bytes(b"opus-1"),
                up_text({"type": "end"}),
            ]
        )
        tracing = RecordingTracing()
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=FakeTranscriber(["Einen Kaffee bitte"]),
            synthesizer=FakeSynth(),
            settings=_settings(),
            tracing=tracing,
        )

        # Setup, the user's turn, and end all scoped to the same thread_id/user_id -
        # this is what lets Langfuse group a whole conversation under one session.
        assert tracing.calls == [
            ("t1", "hanzala"),
            ("t1", "hanzala"),
            ("t1", "hanzala"),
        ]
    finally:
        await conn.close()


async def test_each_turn_flushes_traces_without_waiting_for_process_exit(tmp_path):
    """#55: a live session must not rely on the process exiting for spans to
    reach Langfuse - each turn (setup + every user turn) flushes on its own."""
    import asyncio

    from app.voice import session as session_module

    graph, conn, _ = await build_graph(tmp_path, replies=["Guten Tag!", "Bitte schön."])
    try:
        ws = FakeWS(
            [
                up_text({"type": "start", "scenario_id": "cafe", "thread_id": "t9"}),
                up_bytes(b"opus-1"),
                up_text({"type": "end"}),
            ]
        )
        tracing = RecordingTracing()
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=FakeTranscriber(["Einen Kaffee bitte"]),
            synthesizer=FakeSynth(),
            settings=_settings(),
            tracing=tracing,
        )
        # Flushes are scheduled fire-and-forget on a worker thread; wait for the
        # ones this session queued so the assertion isn't a timing race.
        if session_module._pending_flushes:
            await asyncio.gather(*list(session_module._pending_flushes))

        # One flush per _drive_turn call: the opening line and the user's turn.
        # `end` doesn't drive a turn, so it schedules no extra flush.
        assert tracing.flush_count == 2
    finally:
        await conn.close()


async def test_disabled_tracing_schedules_no_flush(tmp_path):
    """When Langfuse isn't configured, flushing must stay a true no-op - no
    thread spun up for a client that doesn't exist."""
    graph, conn, _ = await build_graph(tmp_path, replies=["Guten Tag!"])
    try:
        ws = FakeWS(
            [up_text({"type": "start", "scenario_id": "cafe", "thread_id": "t10"})]
        )
        tracing = RecordingTracing(enabled=False)
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=FakeTranscriber([]),
            synthesizer=FakeSynth(),
            settings=_settings(),
            tracing=tracing,
        )
        assert tracing.flush_count == 0
    finally:
        await conn.close()


async def test_no_tracing_arg_defaults_to_a_disabled_noop(tmp_path):
    """run_voice_session must work without a tracing kwarg at all - the default
    is a disabled Tracing(), not a crash on the missing dependency."""
    graph, conn, _ = await build_graph(tmp_path, replies=["Guten Tag!"])
    try:
        ws = FakeWS(
            [
                up_text({"type": "start", "scenario_id": "cafe", "thread_id": "t1"}),
                up_text({"type": "end"}),
            ]
        )
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=FakeTranscriber([]),
            synthesizer=FakeSynth(),
            settings=_settings(),
        )
        assert _types(ws)[-1] == "session_closed"
    finally:
        await conn.close()


async def test_audio_before_start_is_a_protocol_error(tmp_path):
    graph, conn, _ = await build_graph(tmp_path, replies=[])
    try:
        ws = FakeWS([up_bytes(b"x")])
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=FakeTranscriber([]),
            synthesizer=FakeSynth(),
            settings=_settings(),
        )
        assert ws.sent == [
            {
                "type": "error",
                "stage": "protocol",
                "message": "send a start message first",
            }
        ]
    finally:
        await conn.close()


async def test_stt_failure_is_reported_and_does_not_kill_the_session(tmp_path):
    class BoomSTT:
        async def transcribe(self, audio, *, mimetype="audio/webm", prompt=None):
            raise STTError("groq down")

    graph, conn, _ = await build_graph(tmp_path, replies=[])
    try:
        ws = FakeWS(
            [
                up_text(
                    {"type": "start", "scenario_id": "baeckerei", "thread_id": "t2"}
                ),
                up_bytes(b"bad"),
            ]
        )
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=BoomSTT(),
            synthesizer=FakeSynth(),
            settings=_settings(),
        )
        errors = [m for m in ws.sent if m["type"] == "error"]
        assert errors and errors[0]["stage"] == "stt"
    finally:
        await conn.close()


async def test_silence_prompts_the_user_again(tmp_path):
    graph, conn, _ = await build_graph(tmp_path, replies=[])
    try:
        ws = FakeWS(
            [
                up_text(
                    {"type": "start", "scenario_id": "baeckerei", "thread_id": "t3"}
                ),
                up_bytes(b"silence"),
            ]
        )
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=FakeTranscriber([""]),
            synthesizer=FakeSynth(),
            settings=_settings(),
        )
        stt_errors = [
            m for m in ws.sent if m["type"] == "error" and m["stage"] == "stt"
        ]
        assert stt_errors and "no speech" in stt_errors[0]["message"]
    finally:
        await conn.close()


async def test_turn_cap_closes_the_session(tmp_path):
    graph, conn, _ = await build_graph(tmp_path, replies=["r"])
    try:
        ws = FakeWS(
            [
                up_text(
                    {"type": "start", "scenario_id": "baeckerei", "thread_id": "t4"}
                ),
                up_bytes(b"a"),
            ]
        )
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=FakeTranscriber(["hallo"]),
            synthesizer=FakeSynth(),
            settings=_settings(voice_max_turns=0),
        )
        assert any(m["type"] == "error" and m["stage"] == "limit" for m in ws.sent)
        assert _types(ws)[-1] == "session_closed"
    finally:
        await conn.close()


async def test_unknown_control_message_is_reported(tmp_path):
    graph, conn, _ = await build_graph(tmp_path, replies=[])
    try:
        ws = FakeWS([up_text({"type": "nonsense"})])
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=FakeTranscriber([]),
            synthesizer=FakeSynth(),
            settings=_settings(),
        )
        assert ws.sent == [
            {
                "type": "error",
                "stage": "protocol",
                "message": "unknown message 'nonsense'",
            }
        ]
    finally:
        await conn.close()


async def test_malformed_json_is_a_protocol_error_not_a_crash(tmp_path):
    graph, conn, _ = await build_graph(tmp_path, replies=[])
    try:
        ws = FakeWS([{"type": "websocket.receive", "text": "{not json"}])
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=FakeTranscriber([]),
            synthesizer=FakeSynth(),
            settings=_settings(),
        )
        assert ws.sent == [
            {
                "type": "error",
                "stage": "protocol",
                "message": "message is not valid JSON",
            }
        ]
    finally:
        await conn.close()


async def test_unknown_scenario_in_start_is_a_protocol_error(tmp_path):
    graph, conn, factory = await build_graph(tmp_path, replies=[])
    try:
        ws = FakeWS(
            [
                up_text(
                    {
                        "type": "start",
                        "scenario_id": "does-not-exist",
                        "thread_id": "t5",
                    }
                )
            ]
        )
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=FakeTranscriber([]),
            synthesizer=FakeSynth(),
            settings=_settings(),
        )
        assert _types(ws) == ["error"]
        assert ws.sent[0]["stage"] == "protocol"
        # No session row was created for the bad scenario.
        with factory() as db:
            assert db.query(Session).count() == 0
    finally:
        await conn.close()


async def test_provider_failure_mid_turn_degrades_without_killing_the_socket(tmp_path):
    class BoomTutor:
        async def reply(self, *, scenario, level, history, writer=None):
            raise RuntimeError("all providers down")

    engine = make_engine(f"sqlite:///{tmp_path / 'w.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    deps = SessionGraphDeps(
        tutor=BoomTutor(),
        collector=CorrectorCollector(_NoErrorCorrector()),
        persister=SessionWriter(session_factory=factory),
    )
    conn = await aiosqlite.connect(str(tmp_path / "ck.db"))
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    graph = build_session_graph(deps).compile(checkpointer=saver)
    try:
        ws = FakeWS(
            [
                up_text(
                    {"type": "start", "scenario_id": "baeckerei", "thread_id": "t6"}
                ),
                up_bytes(b"audio"),
            ]
        )
        await run_voice_session(
            ws,
            graph=graph,
            transcriber=FakeTranscriber(["hallo"]),
            synthesizer=FakeSynth(),
            settings=_settings(),
        )
        # The failing turn reported an error and still closed with turn_done; the
        # loop ran to the end of the inbox instead of the socket tearing down.
        assert any(m["type"] == "error" and m["stage"] == "tutor" for m in ws.sent)
        assert _types(ws)[-1] == "turn_done"
    finally:
        await conn.close()


async def test_set_rate_changes_the_rate_of_the_next_turn_only(tmp_path):
    graph, conn, _ = await build_graph(tmp_path, replies=["Erste.", "Zweite."])
    try:
        ws = FakeWS(
            [
                up_text(
                    {"type": "start", "scenario_id": "baeckerei", "thread_id": "t7"}
                ),
                up_bytes(b"a"),
                up_text({"type": "set_rate", "rate": 0.7}),
                up_bytes(b"b"),
                up_text({"type": "end"}),
            ]
        )

        rates_seen = []

        class RecordingSynth(FakeSynth):
            async def synth(self, text, *, rate=1.0):
                rates_seen.append(rate)
                async for chunk in super().synth(text, rate=rate):
                    yield chunk

        await run_voice_session(
            ws,
            graph=graph,
            transcriber=FakeTranscriber(["hallo", "tschuess"]),
            synthesizer=RecordingSynth(),
            settings=_settings(),
        )

        # The opening line (2 sentences) + first reply spoke at the default rate;
        # only the second reply, spoken after set_rate, used the new one.
        assert rates_seen == [1.0, 1.0, 1.0, 0.7]
    finally:
        await conn.close()


async def test_set_rate_with_a_bad_value_is_ignored_not_a_protocol_error(tmp_path):
    graph, conn, _ = await build_graph(tmp_path, replies=["Antwort."])
    try:
        ws = FakeWS(
            [
                up_text(
                    {"type": "start", "scenario_id": "baeckerei", "thread_id": "t8"}
                ),
                up_text({"type": "set_rate", "rate": "not-a-number"}),
                up_bytes(b"a"),
                up_text({"type": "end"}),
            ]
        )

        rates_seen = []

        class RecordingSynth(FakeSynth):
            async def synth(self, text, *, rate=1.0):
                rates_seen.append(rate)
                async for chunk in super().synth(text, rate=rate):
                    yield chunk

        await run_voice_session(
            ws,
            graph=graph,
            transcriber=FakeTranscriber(["hallo"]),
            synthesizer=RecordingSynth(),
            settings=_settings(),
        )

        assert not any(m["type"] == "error" for m in ws.sent)
        assert all(r == 1.0 for r in rates_seen)
    finally:
        await conn.close()


async def test_reconnect_on_same_thread_resumes_without_replaying_setup(tmp_path):
    graph, conn, _ = await build_graph(
        tmp_path, replies=["Erste Antwort.", "Zweite Antwort."]
    )
    try:
        ws1 = FakeWS(
            [
                up_text(
                    {"type": "start", "scenario_id": "nachbar", "thread_id": "keep"}
                ),
                up_bytes(b"a"),
            ]
        )
        await run_voice_session(
            ws1,
            graph=graph,
            transcriber=FakeTranscriber(["hallo"]),
            synthesizer=FakeSynth(),
            settings=_settings(),
        )

        # New connection, same thread: start must resume, not re-run setup.
        ws2 = FakeWS(
            [
                up_text(
                    {"type": "start", "scenario_id": "nachbar", "thread_id": "keep"}
                ),
                up_bytes(b"b"),
            ]
        )
        await run_voice_session(
            ws2,
            graph=graph,
            transcriber=FakeTranscriber(["tschuess"]),
            synthesizer=FakeSynth(),
            settings=_settings(),
        )

        # The reconnect emitted ready but did NOT stream another opening line.
        assert _types(ws2)[0] == "ready"
        assert not any(
            m.get("text") == get_scenario("nachbar").opening_line
            for m in ws2.sent
            if m["type"] == "reply_token"
        )

        # State carries the whole conversation: opening (once) + both turns.
        snapshot = await graph.aget_state(_cfg("keep"))
        contents = [m["content"] for m in snapshot.values["messages"]]
        opening = get_scenario("nachbar").opening_line
        assert contents.count(opening) == 1
        assert "hallo" in contents and "tschuess" in contents
        assert contents[-1] == "Zweite Antwort."
    finally:
        await conn.close()
