"""Tracing that can never break a session (guardrail #4).

A thin wrapper over the Langfuse observation API. When Langfuse is unconfigured
(no client) it is a silent no-op; when it is configured but the SDK throws -
starting a span, closing it, or flushing - the error becomes a log line, never an
exception the caller sees. Agents and the provider wrap their LLM calls in
`generation(...)` and set the output/model via the yielded handle.
"""

import logging
from contextlib import contextmanager
from typing import Iterator

logger = logging.getLogger(__name__)


class _Handle:
    """What `generation(...)` yields. `update` records output/model/usage on the
    span, or does nothing when tracing is off or the span failed to start."""

    def __init__(self, span) -> None:
        self._span = span

    def update(self, **fields) -> None:
        if self._span is None:
            return
        try:
            self._span.update(**fields)
        except Exception as exc:
            logger.warning("Trace update failed, ignoring: %s", exc)


class Tracing:
    """Records generations to Langfuse. Pass `client=None` for a disabled no-op;
    build_tracing() wires the shared client when Langfuse is configured."""

    def __init__(self, client=None) -> None:
        self._client = client

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @contextmanager
    def generation(
        self,
        *,
        name: str,
        input=None,
        model: str | None = None,
        metadata: dict | None = None,
    ) -> Iterator[_Handle]:
        if self._client is None:
            yield _Handle(None)
            return

        # Enter by hand rather than `with`, so a failure to start degrades to a
        # no-op handle instead of raising into the caller. Per the context-manager
        # protocol, if __enter__ raises we must NOT call __exit__.
        try:
            cm = self._client.start_as_current_observation(
                as_type="generation",
                name=name,
                input=input,
                model=model,
                metadata=metadata,
            )
            span = cm.__enter__()
        except Exception as exc:
            logger.warning("Tracing disabled for this call, could not start: %s", exc)
            yield _Handle(None)
            return

        try:
            yield _Handle(span)
        finally:
            try:
                cm.__exit__(None, None, None)
            except Exception as exc:
                logger.warning("Trace close failed, ignoring: %s", exc)

    def flush(self) -> None:
        """Send pending spans. Call at shutdown / end of a short-lived request."""
        if self._client is None:
            return
        try:
            self._client.flush()
        except Exception as exc:
            logger.warning("Trace flush failed, ignoring: %s", exc)


def build_tracing() -> Tracing:
    """App-wide tracing over the shared Langfuse client (or a disabled no-op)."""
    from app.llmops.client import get_langfuse

    return Tracing(client=get_langfuse())
