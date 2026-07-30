"""The assembled session runtime: the compiled, checkpointed graph plus the
resources it owns.

This is the one object the app builds at startup and the voice loop (#8) drives:
one LLM provider, one Tutor over the prompt store, the learner-store writer, and a
graph compiled against a checkpointer on its own SQLite file. It owns the two
things with a lifecycle - the provider's HTTP client and the checkpointer's
connection - and closes them on shutdown, so nothing leaks between restarts.

Built once and reused: the graph is stateless across sessions (per-conversation
state lives in the checkpoint, keyed by thread_id), so a single runtime serves
every session.
"""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

from app.agents.checkpointer import checkpoints_path, open_checkpointer
from app.agents.corrector import Corrector, CorrectorCollector
from app.agents.graph import SessionGraphDeps, build_session_graph
from app.agents.persistence import SessionWriter
from app.agents.tutor import Tutor
from app.config import Settings, get_settings
from app.llm.provider import LLMProvider
from app.llmops.prompts import build_prompt_store
from app.llmops.tracing import Tracing, build_tracing


@dataclass
class SessionRuntime:
    graph: object  # a compiled LangGraph; typed loosely to avoid a hard import here
    tracing: Tracing  # shared with the provider's tracing; used to scope traces per session (#51)
    collector: CorrectorCollector  # same instance the graph nodes submit to; the voice
    # loop calls collector.discard() on a disconnect without `end` (#48)
    _provider: LLMProvider
    _conn: aiosqlite.Connection

    async def aclose(self) -> None:
        """Release the checkpointer connection and the provider's HTTP client.
        Ordered close, both best-effort, so shutdown never hangs on one of them."""
        try:
            await self._conn.close()
        finally:
            await self._provider.aclose()


async def build_session_runtime(settings: Settings | None = None) -> SessionRuntime:
    """Wire the whole session runtime. LLM calls are traced (a no-op when Langfuse
    is unconfigured); prompts come from the store (Langfuse + bundled fallback);
    persistence uses the app's learner store."""
    settings = settings or get_settings()
    tracing = build_tracing()
    provider = LLMProvider(settings=settings, tracing=tracing)
    prompt_store = build_prompt_store()
    collector = CorrectorCollector(
        Corrector(
            provider=provider,
            prompt_store=prompt_store,
            prompt_label=settings.corrector_prompt_label,
            temperature=settings.corrector_temperature,
            max_tokens=settings.corrector_max_tokens,
        ),
        severity_threshold=settings.corrector_severity_threshold,
    )
    deps = SessionGraphDeps(
        tutor=Tutor(
            provider=provider,
            prompt_store=prompt_store,
            prompt_label=settings.tutor_prompt_label,
            temperature=settings.tutor_temperature,
            max_tokens=settings.tutor_max_tokens,
        ),
        collector=collector,
        persister=SessionWriter(),
    )
    saver, conn = await open_checkpointer(checkpoints_path(settings))
    graph = build_session_graph(deps).compile(checkpointer=saver)
    return SessionRuntime(
        graph=graph,
        tracing=tracing,
        collector=collector,
        _provider=provider,
        _conn=conn,
    )
