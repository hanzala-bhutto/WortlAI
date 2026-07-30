# Feasibility: 048 - Clean up CorrectorCollector tasks for sessions abandoned without an end

- **Issue**: #48
- **Phase / Milestone**: Phase 1 - Voice Conversation Loop
- **Date**: 2026-07-30
- **Author**: Claude (reviewed by Hanzala)

## Goal
`CorrectorCollector` (`backend/app/agents/corrector.py`) tracks spawned
Corrector analysis tasks in `_tasks: dict[session_id, list[Task]]`. A session
that sends `end` always routes through the graph's `debrief` node, which
calls `collector.collect(session_id)` and pops the entry. A client that
drops the WebSocket without sending `end` never reaches `debrief`, so its
task list is never popped: `_tasks` grows unbounded over a long-lived
process, and if a leaked task raised (e.g. every LLM provider was down),
Python logs "Task exception was never retrieved" at GC since nothing ever
awaits or reads its exception.

## Approach
**Add `CorrectorCollector.discard(session_id)`, called from
`run_voice_session`'s disconnect path in `backend/app/voice/session.py`.**

- `discard` pops `_tasks[session_id]` (mirroring `collect`'s pop, so the
  dict never grows past live sessions) but does not await the tasks - the
  loop must return immediately on disconnect, not block on in-flight
  analysis. Instead it attaches a `done_callback` to each task that reads
  `task.exception()` and logs it if present, which both retrieves the
  exception (silencing "Task exception was never retrieved") and gives
  visibility into a provider outage without raising into the event loop.
  Tasks that are already finished get their callback invoked on the next
  loop tick; tasks still running finish naturally in the background and are
  simply never persisted anywhere - correct, since there is no session row
  left to attach a debrief to.
- `run_voice_session` gains an optional `collector: CorrectorCollector |
  None = None` parameter (default `None` keeps every existing call site and
  test that doesn't care about cleanup working unchanged). On the
  `websocket.disconnect` branch, if a `thread_id` was established and a
  collector was passed, look up the session id via
  `graph.aget_state(_cfg(thread_id))` (same pattern already used by the
  `end` handler) and call `collector.discard(session_id)`.
- `app/api/v1/voice.py` passes `runtime.collector` (new field on
  `SessionRuntime`, populated from `SessionGraphDeps.collector` in
  `build_session_runtime` - the same `CorrectorCollector` instance the graph
  nodes already submit to, not a second one).

## Alternatives considered
- **Await-and-drop instead of a done_callback**: `await task` inside the
  disconnect branch retrieves the exception too, but blocks `run_voice_session`
  (and therefore the WebSocket handler's return) on however long the
  Corrector call takes - a needless delay on a path that exists precisely to
  close out fast. Rejected in favor of the non-blocking callback.
- **Cancel the tasks instead of letting them finish**: stops the in-flight
  Groq call, freeing free-tier throughput slightly sooner, but adds
  `CancelledError` handling and buys nothing observable (no debrief will
  ever consume the result either way). Rejected: no measured need, more
  code.
- **Periodic sweep of stale entries** (e.g. a background task that discards
  anything older than N minutes): decouples cleanup from the disconnect
  event, but needs a timestamp per entry and a sweep loop to manage for a
  problem the disconnect handler already sees directly. Rejected as
  unneeded complexity - the disconnect message is the exact signal to react
  to.

## Risks & unknowns
- `graph.aget_state` after a disconnect could itself fail if the
  checkpointer connection is already closing (e.g. during shutdown); wrapped
  so a lookup failure just skips the discard rather than raising out of the
  disconnect branch (guardrail #4 - cleanup must never crash the session
  teardown).
- A session that never got past `start` (no `session_id` in state yet, e.g.
  disconnect before the first turn) has nothing to discard - `discard` on a
  session id that was never submitted is a no-op via `dict.pop(..., [])`,
  same as `collect`.

## Free-tier impact
None - purely local bookkeeping; the abandoned Corrector call itself was
already in flight and already billed regardless of whether `discard` is
added.

## Effort estimate
S (<2h): add `discard` to `CorrectorCollector`, add `collector` to
`SessionRuntime`/`build_session_runtime`, thread the optional parameter
through `run_voice_session` and its disconnect branch, and a regression test
driving a scripted disconnect-without-`end` that asserts `_tasks` holds no
entry for the session afterward and that a failing analysis logs rather than
warns "Task exception was never retrieved".

## Verdict
**GO**.
