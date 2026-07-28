# Feasibility: 051 - Langfuse trace scoping (session_id/user_id)

- **Issue**: #51
- **Phase / Milestone**: Phase 1 - Voice Conversation Loop
- **Date**: 2026-07-27
- **Author**: Claude (reviewed by Hanzala)

## Goal
Every Langfuse trace from the voice loop currently ships with `sessionId=null`
and `userId=null` (49/49 traces observed), so conversations can't be told apart
in the Langfuse UI. Tag each trace with `session_id=thread_id` and a fixed
`user_id`, so a session's spans group under one filterable session.

## Approach options
1. **`langfuse.propagate_attributes(session_id=, user_id=)` as a context
   manager around each turn (chosen)** - confirmed via direct inspection of the
   installed `langfuse==4.14.1` package (OTEL-based v4 SDK; the docs-era
   `update_current_trace()` does not exist in this version). It's a
   module-level function, not a `Langfuse` client method, and propagates via
   OTEL baggage/context - including across `asyncio.create_task()` boundaries,
   so the Corrector's fire-and-forget analysis task (created inside a turn)
   inherits the tag without threading session_id through its call signature.
   Wrap it in a new `Tracing.session(...)` method with the same
   disabled-when-no-client / swallow-SDK-exceptions guard `Tracing.generation()`
   already uses (guardrail #4: tracing must never break a session).
2. Thread `session_id`/`user_id` explicitly through every `LLMProvider.complete
   /stream` call signature and `Tracing.generation()` - rejected: touches every
   call site, and OTEL context propagation makes it unnecessary.
3. An explicit root span per turn (`start_as_current_span` wrapping the graph
   invocation) in addition to session_id tagging - deferred, not rejected. The
   acceptance criteria ("traces group under a single trace, filterable by
   session id") may already be satisfied by Langfuse's Sessions view grouping
   sibling traces that share a `session_id`, without a literal shared root
   span. Verify live against the local instance once #51 lands; add a root
   span only if the Sessions view doesn't group as expected.

## Risks & unknowns
- Whether `propagate_attributes` alone produces true single-trace nesting or
  Sessions-view grouping of separate traces → **verified live 2026-07-27**:
  Sessions-view grouping. Two traces tagged via `Tracing.session(session_id=
  "live-check-51-manual", user_id="hanzala")` both carry that session_id/
  user_id and `GET /api/public/sessions` returns one session grouping both -
  confirmed against the local Langfuse instance via its public API, not just
  the SDK. No root span needed; option 3 above is dropped.
- Live-verifying through a real running app turned up a separate, pre-existing
  bug (filed as #55): nothing in the app calls `Tracing.flush()`, so the OTEL
  batch processor never actually sends spans during a live session - only an
  explicit flush does. #51's tagging logic is correct (proven via manual
  flush); #55 is what makes traces visible at all outside of tests.
- `user_id` has no existing concept in this single-user app → add
  `settings.langfuse_user_id` (default `"hanzala"`), a tunable per the
  project's `.env` convention rather than a hardcoded string, wired correctly
  per the acceptance criteria without over-building multi-user support.

## Free-tier impact
None - Langfuse is self-hosted; this only adds metadata to spans already being
sent.

## Effort estimate
S (<2h): one new `Tracing` method, one new setting, two call sites in
`voice/session.py` (`_drive_turn` and the `end` path), plus tests for the
contextvar propagation.

## Verdict
**GO**.
