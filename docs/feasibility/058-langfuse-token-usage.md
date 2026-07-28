# Feasibility: 058 - Langfuse token usage capture

- **Issue**: #58
- **Phase / Milestone**: Phase 1 - Voice Conversation Loop
- **Date**: 2026-07-28
- **Author**: Claude (reviewed by Hanzala)

## Goal
Every Langfuse generation from this app ships with an empty `usageDetails` and
zeroed prompt/completion/total tokens, verified live against two real sessions
(2026-07-28). Read Groq's `usage` object off both the non-streamed and streamed
completion paths in `LLMProvider` and forward it to the tracing handle, so a
generation's token counts are visible in Langfuse.

## Approach options
1. **Thread usage through the existing call sites, remapped to Langfuse's
   canonical keys (chosen)** - confirmed via direct inspection of the
   installed `langfuse==4.14.1` SDK (`LangfuseGeneration.update` takes
   `usage_details: Dict[str, int] | None`). The initial implementation passed
   Groq's raw OpenAI-shaped usage dict (`prompt_tokens`/`completion_tokens`
   /`total_tokens`) straight through, mirroring `langfuse/openai.py:685` - but
   **live-verifying against the actual self-hosted instance disproved this**:
   the server does not alias OpenAI key names to its own canonical fields, so
   `promptTokens`/`completionTokens` stayed `0` and `total` was computed by
   summing all three raw keys (double-counting, since `total_tokens` already
   includes prompt+completion). Fixed by adding `_to_langfuse_usage()`, which
   maps to `{"input": prompt_tokens, "output": completion_tokens, "total":
   total_tokens}` - verified live afterward to populate `promptTokens`/
   `completionTokens`/`totalTokens` correctly with no double-counting. The
   rest of the plumbing:
   - `_complete_one` returns `(content, usage)` instead of just `content`;
     `usage` is `response.json().get("usage")` run through
     `_to_langfuse_usage`, `None` if absent or the body is malformed.
   - `complete()` passes it: `gen.update(output=reply, model=target.model,
     usage_details=usage)`.
   - `_payload()` adds `"stream_options": {"include_usage": True}` whenever
     `stream=True`, which is the documented way to get a final usage-only SSE
     chunk from an OpenAI-compatible streaming endpoint.
   - `_iter_sse` takes a mutable `usage_box: dict` argument and records
     `_to_langfuse_usage(chunk["usage"])` when a chunk carries one (the final
     chunk has empty `choices` and a `usage` key); `stream()` reads it back
     after the token loop completes and forwards it the same way.
2. Have `LLMProvider` return usage to callers (`voice/session.py` etc.) and let
   them attach it - rejected: nothing downstream of the provider needs raw
   token counts today, and it would spread Groq-shaped response parsing
   outside the one file guardrail #keep-deps-behind-one-file protects.
3. Compute usage client-side by counting tokens (tiktoken-style) - rejected:
   Groq already returns exact counts for free; approximating them would be
   strictly worse and adds a new dependency for no reason.

## Risks & unknowns
- Whether Groq's OpenAI-compatible endpoint actually honors
  `stream_options.include_usage` and emits the extra chunk → **verified live**:
  it does. A real streamed turn (`llama-3.3-70b-versatile`, since gpt-oss-120b
  returned an empty completion at `max_tokens=30` and fell back per the
  existing empty-content handling) produced a trailing chunk with `usage` and
  empty `choices`, captured correctly. If Groq ever stops honoring it, the
  streaming path degrades to no usage (same as before this issue), not a
  crash - `usage_box["usage"]` simply stays `None` and `gen.update(...,
  usage_details=None)` is a no-op field per the SDK signature.
- Whether Langfuse's `usage_details` recognizes OpenAI-style key names →
  **verified live it does not** (self-hosted instance, SDK 4.14.1): see
  approach option 1 above. Confirmed by sending three variant shapes directly
  to a throwaway test generation - only `input`/`output`/`total` keys populate
  `promptTokens`/`completionTokens`; `prompt_tokens`/`completion_tokens` leave
  those columns at `0`.
- A malformed or usage-less JSON body must not raise past the existing
  malformed-content handling in `_extract_content` - handled by reading usage
  with `.get("usage")` rather than indexing, so a missing key is `None`, not
  an exception.
- Guardrail #4 (tracing never breaks a session): unaffected - `usage` is just
  another field passed into the same `Tracing._Handle.update()`, which already
  swallows every SDK exception.

## Free-tier impact
None - this only adds fields to Langfuse spans already being sent
(self-hosted, no quota) and one extra param on requests already being made to
Groq. No new requests, no new tokens billed.

## Effort estimate
S (<2h): two call sites in `provider.py` (`complete`/`_complete_one` and
`stream`/`_stream_one`/`_iter_sse`), plus tests asserting usage is read from a
mocked response and forwarded to a fake tracing handle.

## Verdict
**GO**.
