"""Contract for the FastAPI voice route adapter (app/api/v1/voice.py).

The protocol itself is tested in test_voice_session against the real graph. Here we
only pin the adapter's job: accept the socket, hand run_voice_session the graph and
pipeline off app.state, and swallow a WebSocketDisconnect so a dropped mic is a
normal end, not a 500. A no-turn connection never touches the graph, so plain stubs
suffice - no DB, no event-loop juggling.
"""

from types import SimpleNamespace

from fastapi import WebSocketDisconnect

from app.api.v1.voice import voice_stream


def _app():
    return SimpleNamespace(
        state=SimpleNamespace(
            session_runtime=SimpleNamespace(graph=object()),
            voice_pipeline=SimpleNamespace(transcriber=object(), synthesizer=object()),
        )
    )


class RouteWS:
    def __init__(self, app, inbox):
        self.app = app
        self._inbox = list(inbox)
        self.accepted = False
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def receive(self):
        if self._inbox:
            item = self._inbox.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return {"type": "websocket.disconnect"}

    async def send_json(self, data):
        self.sent.append(data)


async def test_route_accepts_and_returns_cleanly_on_disconnect():
    ws = RouteWS(_app(), inbox=[])  # nothing sent -> immediate disconnect frame
    await voice_stream(ws)
    assert ws.accepted


async def test_route_swallows_websocket_disconnect_exception():
    # A mid-session drop surfaces as WebSocketDisconnect from receive(); the route
    # must catch it, not let it become an error.
    ws = RouteWS(_app(), inbox=[WebSocketDisconnect(code=1000)])
    await voice_stream(ws)  # must not raise
    assert ws.accepted
