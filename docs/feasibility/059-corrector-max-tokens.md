# Feasibility: 059 - Corrector max_tokens truncates JSON output

- **Issue**: #59
- **Phase / Milestone**: Phase 1 - Voice Conversation Loop
- **Date**: 2026-07-29
- **Author**: Claude (reviewed by Hanzala)

## Goal
`Corrector.analyze()` (`backend/app/agents/corrector.py`) sometimes gets back
truncated JSON from `openai/gpt-oss-120b`, burning guardrail #1's one retry -
and the retry can truncate too, dropping the batch entirely. Reproduced live
twice on 2026-07-28 with short, single-error utterances, so this isn't a
volume problem: `DEFAULT_MAX_TOKENS = 512` (`corrector.py:50`) is being spent
on something other than the visible JSON.

## Approach
**`reasoning_effort="low"` on the Corrector's own calls, plus raising
`DEFAULT_MAX_TOKENS` as a buffer, plus a cheap truncation-specific log line
(chosen) - not a provider-wide change.**

- Groq's docs confirm `gpt-oss-120b` accepts a `reasoning_effort` parameter
  (`low`/`medium`/`high`) that controls how much hidden chain-of-thought the
  model emits before the visible completion, and that this reasoning is
  billed against the same `max_tokens`/`max_completion_tokens` budget as the
  answer - matching the issue's "reasoning model, hidden CoT counted against
  the same budget" theory. Groq's own community forum has an open report of
  reasoning tokens still leaking through even when a caller asks to hide
  them, so `low` effort is treated as *reducing* the problem, not eliminating
  it - `max_tokens` still needs headroom.
- `LLMProvider._payload` (`llm/provider.py:143`) gains an optional
  `reasoning_effort: str | None = None` param, included in the payload only
  when set, so Tutor's calls (which want full reasoning quality) are
  unaffected. `LLMProvider.complete`/`.stream` gain a matching optional
  kwarg, threaded straight into `_payload`. `Corrector.analyze()` passes
  `reasoning_effort="low"` on both the first attempt and the retry - a
  correction report is short, mechanical classification, not a task that
  benefits from deep reasoning.
- `DEFAULT_MAX_TOKENS` goes from 512 to 1024. This is still a guess (option 1
  from the issue), but combined with `low` effort it's a guess with real
  headroom instead of the current guess with none. Groq exposes no way to
  cap reasoning tokens independently of the shared budget, so "raise the
  ceiling enough that a low-effort reasoning preamble plus a 1-2 error JSON
  payload both fit" is the only lever available beyond effort level.
- Truncation-specific diagnosability (option 3), scoped down: `_extract_output`
  already discards the distinction between "no JSON object found", "found
  one but it's syntactically incomplete", and "found one but a field failed
  Pydantic validation". Add a narrow check in `Corrector.analyze()`'s
  drop-path - if `raw` is non-empty and doesn't contain a balanced `{...}`
  (the regex found no match, or `json.JSONDecodeError` fired at/near the end
  of the string) - log `"...likely truncated at N chars"` instead of the
  generic malformed-output warning. This needs no change to the shared
  `LLMProvider` (`finish_reason` stays unplumbed - that's a bigger change
  touching Tutor's path too, and isn't needed once truncation is inferred
  from the JSON shape itself, which is already available at the call site).

## Risks & unknowns
- `reasoning_effort="low"` could weaken the Corrector's classification
  quality (e.g. missing a subtler grammar error) - this is exactly what the
  regression test and issue's two reproduced utterances check for: both
  must still classify correctly, not just parse. If low effort measurably
  degrades quality once eval'd (issue #28's gold dataset harness will make
  this visible later), the fix is to raise effort back to `medium` and lean
  on the larger `max_tokens` alone.
- Groq's docs don't state a default `reasoning_effort` for gpt-oss-120b or
  confirm the exact accounting of hidden tokens against `max_tokens` -
  reasoning here is from the model card plus the community bug report, not a
  guarantee. If `low` + 1024 still truncates in practice, the next lever is
  `max_tokens` alone (option 1), now diagnosable via the new log line
  instead of requiring a Langfuse trace pull.
- Threading `reasoning_effort` through `LLMProvider` touches a file every
  agent depends on - kept additive and optional (`None` = today's behavior)
  so Tutor, and any future caller, are unaffected unless they opt in.

## Free-tier impact
Slightly cheaper per Corrector call on average (low effort emits fewer
hidden tokens than the current unset/default effort), offset by the raised
ceiling only mattering on the rare turn that would otherwise have
truncated-and-retried (i.e. burned 2 calls) anyway. Net neutral to positive
against the Groq free tier.

## Effort estimate
S (<2h): one optional param threaded through `provider.py` (3 call sites),
`DEFAULT_MAX_TOKENS` bump plus `reasoning_effort="low"` on both Corrector
call sites, the truncation-log branch in the drop-path, and a regression
test with a deliberately long/multi-error Corrector response mocked to
truncate at the old 512-token limit but succeed at 1024.

## Verdict
**GO**.
