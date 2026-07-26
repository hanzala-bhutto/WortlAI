"""Contract for the tracing wrapper.

The one rule that outranks everything else: tracing must never break a session
(guardrail #4). So the wrapper is a no-op when Langfuse is not configured, and it
swallows every error the SDK can throw - starting a span, closing it, flushing -
turning each into a log line, never an exception the caller sees.

Runs against a fake client, so no network and no real OpenTelemetry.
"""

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
