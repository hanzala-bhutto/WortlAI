"""Contract for the tracing wrapper.

The one rule that outranks everything else: tracing must never break a session
(guardrail #4). So the wrapper is a no-op when Langfuse is not configured, and it
swallows every error the SDK can throw - starting a span, closing it, flushing -
turning each into a log line, never an exception the caller sees.

Runs against a fake client, so no network and no real OpenTelemetry.
"""

from opentelemetry import context as otel_context_api

from app.llmops.tracing import Tracing


class FakeSpan:
    def __init__(self):
        self.updates = []

    def update(self, **kwargs):
        self.updates.append(kwargs)


class FakeCM:
    """A context manager like start_as_current_observation returns, with knobs to
    blow up on enter or exit so we can prove those are swallowed."""

    def __init__(self, span, *, boom_enter=False, boom_exit=False):
        self.span = span
        self.boom_enter = boom_enter
        self.boom_exit = boom_exit
        self.entered = False
        self.exited = False

    def __enter__(self):
        if self.boom_enter:
            raise RuntimeError("enter boom")
        self.entered = True
        return self.span

    def __exit__(self, *_exc):
        self.exited = True
        if self.boom_exit:
            raise RuntimeError("exit boom")
        return False


class FakeClient:
    def __init__(self, cm):
        self.cm = cm
        self.obs_calls = []
        self.flushed = False

    def start_as_current_observation(self, **kwargs):
        self.obs_calls.append(kwargs)
        return self.cm

    def flush(self):
        self.flushed = True


def test_disabled_tracing_is_a_noop_and_never_raises():
    tracing = Tracing(client=None)

    assert tracing.enabled is False
    with tracing.generation(
        name="llm.complete", input=[{"role": "user", "content": "hi"}]
    ) as gen:
        gen.update(output="whatever")  # must not raise
    tracing.flush()  # must not raise


def test_enabled_tracing_records_a_generation():
    span = FakeSpan()
    cm = FakeCM(span)
    client = FakeClient(cm)
    tracing = Tracing(client=client)

    assert tracing.enabled is True
    with tracing.generation(
        name="llm.complete",
        input=[{"role": "user"}],
        metadata={"temperature": 0.7},
    ) as gen:
        gen.update(output="Guten Tag", model="openai/gpt-oss-120b")

    call = client.obs_calls[0]
    assert call["as_type"] == "generation"
    assert call["name"] == "llm.complete"
    assert cm.entered and cm.exited
    assert {"output": "Guten Tag", "model": "openai/gpt-oss-120b"} in span.updates
    tracing.flush()
    assert client.flushed is True


def test_start_errors_degrade_to_noop():
    client = FakeClient(FakeCM(FakeSpan(), boom_enter=True))
    tracing = Tracing(client=client)

    with tracing.generation(name="x") as gen:
        gen.update(output="still fine")  # handle is a no-op, must not raise


def test_close_errors_are_swallowed():
    cm = FakeCM(FakeSpan(), boom_exit=True)
    tracing = Tracing(client=FakeClient(cm))

    with tracing.generation(name="x") as gen:
        gen.update(output="ok")

    assert cm.exited is True  # exit ran, its exception was swallowed


def test_flush_errors_are_swallowed():
    class BoomClient:
        def flush(self):
            raise RuntimeError("cannot flush")

    Tracing(client=BoomClient()).flush()  # must not raise


def test_disabled_session_is_a_noop_and_never_raises():
    tracing = Tracing(client=None)

    with tracing.session(session_id="thread-1", user_id="hanzala"):
        pass  # must not raise, and must not tag the OTEL context

    assert otel_context_api.get_value("langfuse.propagated.session_id") is None


def test_enabled_session_propagates_session_and_user_id():
    tracing = Tracing(client=FakeClient(FakeCM(FakeSpan())))

    with tracing.session(session_id="thread-1", user_id="hanzala"):
        assert (
            otel_context_api.get_value("langfuse.propagated.session_id") == "thread-1"
        )
        assert otel_context_api.get_value("langfuse.propagated.user_id") == "hanzala"

    # The tag does not leak past the block.
    assert otel_context_api.get_value("langfuse.propagated.session_id") is None


def test_session_survives_a_child_task_created_inside_the_block():
    """The Corrector's fire-and-forget analysis task is created inside a turn's
    `tracing.session(...)` block; contextvars snapshot at task-creation time, so
    it must still see the propagated session_id even though it runs after the
    parent block has (or hasn't yet) exited."""
    import asyncio

    tracing = Tracing(client=FakeClient(FakeCM(FakeSpan())))
    seen = {}

    async def _child():
        seen["session_id"] = otel_context_api.get_value(
            "langfuse.propagated.session_id"
        )

    async def _run():
        with tracing.session(session_id="thread-2", user_id="hanzala"):
            task = asyncio.create_task(_child())
            await task

    asyncio.run(_run())
    assert seen["session_id"] == "thread-2"


def test_session_start_errors_degrade_to_noop():
    class BoomOnEnter:
        def __enter__(self):
            raise RuntimeError("enter boom")

        def __exit__(self, *_exc):
            return False

    tracing = Tracing(client=FakeClient(FakeCM(FakeSpan())))
    import app.llmops.tracing as tracing_module

    original = tracing_module.propagate_attributes
    tracing_module.propagate_attributes = lambda **_kwargs: BoomOnEnter()
    try:
        with tracing.session(session_id="thread-1"):
            pass  # must not raise
    finally:
        tracing_module.propagate_attributes = original
