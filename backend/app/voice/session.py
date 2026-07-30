"""The voice loop as a transport-agnostic coroutine.

`run_voice_session` speaks only `receive()` / `send_json()`, the slice of a
WebSocket it actually needs, so it runs against the real Starlette WebSocket in
production and against a scripted fake in tests - in the *same* event loop as the
checkpointer's aiosqlite connection, which the TestClient's separate loop would
break. The FastAPI route in app/api/v1/voice.py is a thin adapter over it.

Protocol (one WebSocket, JSON down, JSON control + binary audio up - see CLAUDE.md):

    up   {"type":"start","scenario_id","level"?,"thread_id"?,"rate"?}  begin a session
    up   <binary audio blob>                                          one spoken turn
    up   {"type":"set_rate","rate"}                                   change speed mid-session
    up   {"type":"end"}                                               close + debrief

    down {"type":"ready","thread_id","scenario_id"}
    down {"type":"transcript","role":"user","text"}     what STT heard
    down {"type":"reply_token","text"}                  Tutor reply, in chunks
    down {"type":"audio","seq","mimetype","data"(b64)}  TTS, per sentence
    down {"type":"turn_done"}                            end of a turn's output
    down {"type":"error","stage","message"}             a degraded, non-fatal step
    down {"type":"session_closed","session_id"?}                     debrief follows

Per-turn model: each turn is one graph invocation resumed from the checkpoint keyed
by thread_id (setup on the first, converse after), exactly as the graph is driven in
test_session_graph. Full lifecycle UI (scenario picker, debrief view) is #8; this
route proves the round-trip and the sentence-level pipelining with the real graph.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Protocol
from uuid import uuid4

from app.agents.scenarios import get_scenario
from app.config import Settings, get_settings
from app.llmops.tracing import Tracing
from app.voice.stt import STTError, Transcriber
from app.voice.tts import AUDIO_MIMETYPE, Synthesizer, split_sentences

logger = logging.getLogger(__name__)

# A completed sentence ends at a terminator that is followed by whitespace or the
# end of the buffer so far. A trailing fragment with no terminator stays buffered,
# so a sentence is never split across two TTS calls mid-word.
_TERMINATOR = re.compile(r"[.!?…]+(?=\s|$)")

# Holds references to fire-and-forget flush tasks (#55) so they aren't garbage
# collected mid-flight - asyncio only keeps a weak reference to a bare
# create_task() result.
_pending_flushes: set[asyncio.Task] = set()


def _schedule_flush(tracing: Tracing) -> None:
    """Send this turn's spans to Langfuse without blocking the voice loop on the
    network round-trip (guardrail #4). `Tracing.flush()` is a synchronous call
    into the SDK, so it runs on a worker thread; any SDK error it raises is
    already swallowed inside `flush()` itself."""
    if not tracing.enabled:
        return
    task = asyncio.create_task(asyncio.to_thread(tracing.flush))
    _pending_flushes.add(task)
    task.add_done_callback(_pending_flushes.discard)


class WSLike(Protocol):
    """The WebSocket slice the loop uses. Starlette's WebSocket satisfies it."""

    async def receive(self) -> dict: ...
    async def send_json(self, data: dict) -> None: ...


async def run_voice_session(
    ws: WSLike,
    *,
    graph: object,
    transcriber: Transcriber,
    synthesizer: Synthesizer,
    settings: Settings | None = None,
    tracing: Tracing | None = None,
) -> None:
    """Drive one voice session to completion (client disconnect or an `end`)."""
    settings = settings or get_settings()
    tracing = tracing or Tracing(client=None)
    thread_id: str | None = None
    scenario_id: str | None = None
    last_reply: str | None = None
    rate = 1.0
    turns = 0

    while True:
        message = await ws.receive()
        if message.get("type") == "websocket.disconnect":
            return

        text = message.get("text")
        if text is not None:
            # The client is untrusted (guardrail 6): malformed JSON, a non-object, or
            # a bad scenario id must degrade to an error frame, never crash the socket.
            try:
                control = json.loads(text)
            except json.JSONDecodeError:
                await _protocol_error(ws, "message is not valid JSON")
                continue
            if not isinstance(control, dict):
                await _protocol_error(ws, "message must be a JSON object")
                continue
            kind = control.get("type")

            if kind == "start":
                scenario_id = control.get("scenario_id")
                if not isinstance(scenario_id, str) or not _scenario_exists(
                    scenario_id
                ):
                    await _protocol_error(
                        ws, f"unknown or missing scenario {scenario_id!r}"
                    )
                    continue
                thread_id = control.get("thread_id") or uuid4().hex
                rate = _coerce_rate(control.get("rate"), settings.voice_rate_default)
                await ws.send_json(
                    {
                        "type": "ready",
                        "thread_id": thread_id,
                        "scenario_id": scenario_id,
                    }
                )
                # Reconnecting to a thread that already has a session: just resume,
                # don't re-run setup - that would replay the opening line and, worse,
                # route to a spurious converse turn. Only a fresh thread gets setup.
                snapshot = await graph.aget_state(_cfg(thread_id))
                if (snapshot.values or {}).get("session_id"):
                    continue
                setup: dict = {"scenario_id": scenario_id}
                if control.get("level"):
                    setup["level"] = control["level"]
                # The opening line streams out of the setup node's writer just like a
                # Tutor reply, so the same turn driver renders and speaks it.
                with tracing.session(
                    session_id=thread_id, user_id=settings.langfuse_user_id
                ):
                    last_reply = await _drive_turn(
                        ws, graph, _cfg(thread_id), setup, synthesizer, rate, tracing
                    )

            elif kind == "set_rate":
                # Applies to every turn from here on (the Sprechtempo slider), silently
                # keeping the current rate on a bad value - same tolerance as `start`.
                rate = _coerce_rate(control.get("rate"), rate)

            elif kind == "end":
                session_id = None
                if thread_id is not None:
                    with tracing.session(
                        session_id=thread_id, user_id=settings.langfuse_user_id
                    ):
                        await graph.ainvoke({"end_requested": True}, _cfg(thread_id))
                        snapshot = await graph.aget_state(_cfg(thread_id))
                    session_id = (snapshot.values or {}).get("session_id")
                await ws.send_json({"type": "session_closed", "session_id": session_id})
                return

            else:
                await _protocol_error(ws, f"unknown message {kind!r}")
            continue

        audio = message.get("bytes")
        if audio is not None:
            if thread_id is None:
                await _protocol_error(ws, "send a start message first")
                continue
            if turns >= settings.voice_max_turns:
                await ws.send_json(
                    {
                        "type": "error",
                        "stage": "limit",
                        "message": "session turn limit reached",
                    }
                )
                await ws.send_json({"type": "session_closed"})
                return

            try:
                transcript = await transcriber.transcribe(
                    audio, prompt=_redemittel_prompt(scenario_id, last_reply)
                )
            except STTError as exc:
                await ws.send_json(
                    {"type": "error", "stage": "stt", "message": str(exc)}
                )
                continue
            if not transcript:
                await ws.send_json(
                    {"type": "error", "stage": "stt", "message": "no speech detected"}
                )
                continue

            turns += 1
            await ws.send_json(
                {"type": "transcript", "role": "user", "text": transcript}
            )
            with tracing.session(
                session_id=thread_id, user_id=settings.langfuse_user_id
            ):
                last_reply = await _drive_turn(
                    ws,
                    graph,
                    _cfg(thread_id),
                    {"user_input": transcript},
                    synthesizer,
                    rate,
                    tracing,
                )


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _scenario_exists(scenario_id: str) -> bool:
    try:
        get_scenario(scenario_id)
        return True
    except KeyError:
        return False


def _redemittel_prompt(scenario_id: str | None, last_reply: str | None) -> str | None:
    """Bias STT toward the scenario's Redemittel plus the Tutor's last reply
    (#52). The static Redemittel only covers a scenario's fixed opening chunks;
    live testing showed the words a learner actually echoes back and mangles
    are often ones the Tutor introduced mid-conversation (a price, an item, a
    payment word) that never appear in that fixed list. None if there is no
    scenario yet and no prior reply to bias with."""
    parts = []
    if scenario_id is not None:
        try:
            scenario = get_scenario(scenario_id)
        except KeyError:
            scenario = None
        if scenario is not None and scenario.redemittel:
            parts.append(", ".join(scenario.redemittel))
    if last_reply:
        parts.append(last_reply)
    if not parts:
        return None
    return " ".join(parts)


def _coerce_rate(value: object, default: float) -> float:
    """A client-sent rate that is missing or not a number falls back to the default
    speed; the actual clamp to the supported band happens in rate_to_percent."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


async def _protocol_error(ws: WSLike, message: str) -> None:
    await ws.send_json({"type": "error", "stage": "protocol", "message": message})


async def _drive_turn(
    ws: WSLike,
    graph: object,
    cfg: dict,
    graph_input: dict,
    synthesizer: Synthesizer,
    rate: float,
    tracing: Tracing,
) -> str:
    """Run one graph turn, streaming its reply chunks to the client and speaking it
    a sentence at a time. TTS fires as soon as a sentence completes, so audio for a
    sentence can play while the next is still being synthesised.

    A turn that blows up (e.g. every LLM provider is down) is a normal path
    (guardrail #4): it degrades to an error frame and a turn_done, so the session
    stays alive for the next utterance rather than the socket tearing down.

    Returns the full reply text (whatever streamed before a failure, if any) so
    the caller can feed it back into the next turn's STT prompt bias (#52) - the
    Tutor's own words are exactly the vocabulary a learner echoes and a hesitant
    utterance is most likely to mangle."""
    buffer = ""
    full_reply: list[str] = []
    seq = 0
    try:
        async for chunk in graph.astream(graph_input, cfg, stream_mode="custom"):
            await ws.send_json({"type": "reply_token", "text": chunk})
            buffer += chunk
            full_reply.append(chunk)
            ready, buffer = _pop_complete_sentences(buffer)
            for sentence in ready:
                seq = await _speak(ws, synthesizer, sentence, rate, seq)
        # Speak whatever the reply ended on without a terminator.
        for sentence in split_sentences(buffer):
            seq = await _speak(ws, synthesizer, sentence, rate, seq)
    except Exception:
        logger.exception("turn failed while driving the graph")
        await ws.send_json(
            {
                "type": "error",
                "stage": "tutor",
                "message": "reply failed, please try again",
            }
        )
    await ws.send_json({"type": "turn_done"})
    # #55: without this, spans from this turn sit in the Langfuse SDK's OTEL
    # batch processor until the process exits - a live session shows nothing.
    _schedule_flush(tracing)
    return "".join(full_reply)


async def _speak(
    ws: WSLike, synthesizer: Synthesizer, sentence: str, rate: float, seq: int
) -> int:
    """Synthesise one sentence and send its audio frames. A synthesis failure is
    logged and skipped (guardrail #4): the learner keeps the text, the turn goes on."""
    try:
        async for audio in synthesizer.synth(sentence, rate=rate):
            await ws.send_json(
                {
                    "type": "audio",
                    "seq": seq,
                    "mimetype": AUDIO_MIMETYPE,
                    "data": base64.b64encode(audio).decode("ascii"),
                }
            )
            seq += 1
    except Exception as exc:
        logger.warning("TTS failed for a sentence, skipping audio: %s", exc)
    return seq


def _pop_complete_sentences(buffer: str) -> tuple[list[str], str]:
    """Everything up to the last completed sentence, plus the trailing fragment
    still being generated (returned so it can be carried into the next chunk)."""
    ends = [m.end() for m in _TERMINATOR.finditer(buffer)]
    if not ends:
        return [], buffer
    cut = ends[-1]
    return split_sentences(buffer[:cut]), buffer[cut:]
