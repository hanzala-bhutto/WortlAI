# Feasibility: 053 - Turn-cap session close skips debrief

- **Issue**: #53
- **Phase / Milestone**: Phase 1 - Voice Conversation Loop
- **Date**: 2026-07-30
- **Author**: Claude (reviewed by Hanzala)

## Goal
Hitting the per-session turn cap (`voice_max_turns`, default 60) in
`run_voice_session` (`backend/app/voice/session.py:181-190`) sends
`session_closed` straight after the `error`/`limit` frame, without ever
invoking `{"end_requested": True}`. That skips the graph's `debrief` node, so
the `Session` row never gets `ended_at`, the Corrector's collected
`ErrorLog` rows never get written, and `session_closed` on this path carries
no `session_id` - a client can't even fetch a debrief for a session that
closed this way.

## Approach
**Reuse the `end` branch's close sequence for the turn-cap branch, factored
into a small helper - not a copy-paste of the three lines.**

- Extract the `end` branch's body (`session.py:161-169`) into a private
  async helper, e.g. `_close_session(ws, graph, thread_id, tracing,
  settings) -> None`, that does the `tracing.session(...)` /
  `graph.ainvoke({"end_requested": True}, ...)` / `aget_state` /
  `send_json({"type": "session_closed", "session_id": ...})` sequence
  exactly as today.
- The turn-cap branch (`session.py:181-190`) sends its existing `error`/
  `limit` frame first (unchanged - the client still needs to know *why* the
  session ended), then calls the same helper instead of directly sending a
  bare `session_closed`.
- `thread_id` is guaranteed non-`None` on the audio-message path (checked at
  `session.py:178` just above), so the helper doesn't need the `if
  thread_id is not None` guard the `end` branch carries for its own reason
  (a client could send `end` before `start`) - the turn-cap call site passes
  a definitely-set `thread_id`.

## Risks & unknowns
- None material - this is a pure refactor-and-reuse of an already-tested
  code path, invoked from one additional call site. The `debrief` node
  itself (session close, `ErrorLog` writes) is exercised today via the
  normal `end` path in `test_session_graph.py` and
  `test_round_trip_start_turn_end`; this fix doesn't change that node.
- Turn-cap now does one extra `graph.ainvoke` + `aget_state` round trip
  before closing, same cost the `end` path already pays - negligible.

## Free-tier impact
None - no additional LLM/STT/TTS calls. `debrief` only writes to the local
learner DB and gathers the already-spawned Corrector task.

## Effort estimate
S (<1h): one helper extraction in `session.py`, one call-site swap, extend
`test_turn_cap_closes_the_session` to assert `session_id` is present and
that the `Session` row has `ended_at` set (mirroring the assertions already
in `test_round_trip_start_turn_end`), plus an `ErrorLog` assertion using a
corrector fake that returns a report (mirroring `FakeCorrector` in
`test_session_graph.py`, not the `_NoErrorCorrector` already in this file).

## Verdict
**GO**.
