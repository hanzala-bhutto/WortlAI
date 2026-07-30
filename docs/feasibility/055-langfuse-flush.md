# Feasibility: 055 - Langfuse traces never flushed during a live session

- **Issue**: #55
- **Phase / Milestone**: Phase 1 - Voice Conversation Loop
- **Date**: 2026-07-30
- **Author**: Claude (reviewed by Hanzala)

## Goal
`Tracing.generation()`/`Tracing.session()` (`backend/app/llmops/tracing.py`)
record spans over the Langfuse SDK's OTEL `BatchSpanProcessor`, which holds
spans in memory and only exports them on an explicit `client.flush()` or
process shutdown. `Tracing.flush()` already exists and works (proven live in
#51's investigation: same call plus an explicit `flush()` appeared in
Langfuse within ~2s), but nothing in the app calls it outside of tests. A
live voice session (`run_voice_session` / `_drive_turn` in
`backend/app/voice/session.py`) never flushes, so traces sit unflushed until
the process exits.

## Approach
**Fire-and-forget flush after each turn, chosen from the issue's first
option, made non-blocking via `asyncio.to_thread`.**

- `_drive_turn` (`backend/app/voice/session.py:249`) gains a `tracing:
  Tracing` parameter, threaded from its three call sites in
  `run_voice_session` (setup turn, converse turn - `_drive_turn` is called
  from inside `with tracing.session(...)` blocks already, so the value is in
  scope at every call site).
- After `turn_done` is sent, schedule the flush rather than awaiting it
  inline: `client.flush()` is a synchronous, blocking network call in the
  Langfuse SDK, and awaiting it in the hot path would violate guardrail #4
  ("tracing must never... noticeably delay a session") even though the flush
  itself never raises. `asyncio.create_task(asyncio.to_thread(tracing.flush))`
  hands the block to a worker thread and returns immediately; the task's
  reference is kept in a module-level `set` with a `done_callback` that
  discards it, so it isn't garbage-collected mid-flight (a bare
  `create_task()` with no held reference is eligible for GC before it runs).
- Skip scheduling entirely when `tracing.enabled` is `False` (Langfuse
  unconfigured) - no thread, no task, just the existing no-op path.
- No change to `Tracing.flush()` itself: it already swallows every SDK
  exception into a log line (guardrail #4), so running it off-thread doesn't
  need new error handling.

## Alternatives considered
- **Periodic background flush task** (issue's second option): decouples
  flush cadence from turn cadence, but adds a long-lived task to manage
  (start/cancel on session end) for no benefit over per-turn flush at this
  traffic volume (one learner, a few turns per session) - per-turn keeps
  trace latency tied to something observable (turn boundaries) instead of an
  arbitrary timer.
- **Shorter `schedule_delay_millis` on the OTEL processor** (issue's third
  option): would remove app-level flushing entirely if viable, but it's an
  SDK-internal knob the issue itself flags as unconfirmed for
  `langfuse==4.14.1`. Not pursued: even if it works, it couples correctness
  to an undocumented internal, whereas an explicit flush call is legible and
  independently testable.
- **Awaiting flush inline before `turn_done`**: simplest, ruled out because
  it puts a real network round-trip on the path the learner is waiting on
  (violates guardrail #4's "never noticeably delay a session").

## Risks & unknowns
- Off-thread flush means a flush could still be "in flight" when the process
  exits (e.g. server restart mid-session) and get dropped - same gap that
  exists today at shutdown, not a regression. Not fixed here; out of scope
  for #55, which is about the live-session case.
- `asyncio.to_thread` spins up a thread pool worker per turn; at Groq
  free-tier session volume (a handful of turns per session, one learner) this
  is negligible. If flush volume ever grows, batching several turns into one
  flush would be the next step - not needed now.

## Free-tier impact
None - Langfuse flush frequency doesn't touch Groq usage; it's a network
call to the self-hosted Langfuse instance only.

## Effort estimate
S (<2h): thread `tracing` into `_drive_turn`'s signature and three call
sites, add the fire-and-forget schedule helper plus its task-reference set,
and a regression test asserting flush is scheduled once per turn using a
fake `Tracing` (extending the existing `RecordingTracing` test double in
`test_voice_session.py`) without relying on process exit.

## Verdict
**GO**.
