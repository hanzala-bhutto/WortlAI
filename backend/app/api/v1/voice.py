"""The voice WebSocket route: a thin adapter over run_voice_session.

All the protocol and turn logic lives in app/voice/session.py so it can be tested
without a real socket. This module only accepts the connection, hands the loop the
graph and pipeline built at startup, and swallows a client disconnect.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.voice.session import run_voice_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["session"])


@router.websocket("/voice/stream")
async def voice_stream(ws: WebSocket) -> None:
    await ws.accept()
    runtime = ws.app.state.session_runtime
    pipeline = ws.app.state.voice_pipeline
    try:
        await run_voice_session(
            ws,
            graph=runtime.graph,
            transcriber=pipeline.transcriber,
            synthesizer=pipeline.synthesizer,
        )
    except WebSocketDisconnect:
        # Normal: the browser closed the tab or dropped the mic. The checkpoint
        # holds the conversation, so the session resumes if they reconnect.
        logger.info("voice client disconnected")
