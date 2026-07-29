# Feasibility: 060 - STT (Whisper) calls invisible in Langfuse

- **Issue**: #60
- **Phase / Milestone**: Phase 1 - Voice Conversation Loop
- **Date**: 2026-07-29
- **Author**: Claude (reviewed by Hanzala)

## Goal
`GroqWhisper.transcribe()` (`backend/app/voice/stt.py`) never touches
tracing: no span, no latency, no failure visibility. Unlike the two chat
models (#58), the voice loop's third external call has zero observability.
Wrap it in `Tracing.generation(...)`, mirroring the pattern already used by
`LLMProvider.complete`/`.stream` in `llm/provider.py`.

## Approach
**Thread a `Tracing` dependency through `GroqWhisper`, same shape as
`LLMProvider` (chosen).**
- `GroqWhisper.__init__` gains `tracing: Tracing | None = None`, defaulting
  to a disabled no-op (`Tracing(client=None)`) exactly like the provider.
- `transcribe()` is split: the public method opens
  `self._tracing.generation(name="stt.transcribe", input={"audio_bytes":
  len(audio), "mimetype": mimetype}, model=settings.stt_model)` around the
  existing body, now moved to a private `_do_transcribe()`. On success,
  `gen.update(output=text)`. On `STTError`, `gen.update(level="ERROR",
  status_message=str(exc))` before re-raising, so a failed call still closes
  a span instead of leaving a gap.
- Whisper is billed by audio seconds, not tokens - `response_format="json"`
  never returns a usage object (only `verbose_json` returns `duration`, and
  the code doesn't request it). No token/usage field is forced; latency and
  status come from the span itself, sizing/mimetype from `input`.
- Raw audio bytes never enter a Langfuse field (guardrail #6) - only the
  byte count and mimetype, both already computed for the
  `max_utterance_bytes` cap.
- `build_voice_pipeline()` (`voice/pipeline.py`) gains an optional
  `tracing: Tracing | None = None` param, defaulting to `build_tracing()`
  (mirrors `build_session_runtime()` in `agents/runtime.py`), and passes it
  into `GroqWhisper(...)`. `app/main.py` needs no change since it calls
  `build_voice_pipeline()` with no args.

## Risks & unknowns
- Whether wrapping the pre-network validation (empty audio, oversize,
  missing key) inside the span is desirable, vs. only wrapping the network
  call - **chosen to wrap the whole method**: those rejections are still
  useful signal (e.g. spotting a client sending oversize blobs), and match
  the issue's "failed transcription still produces a span" acceptance
  criterion without needing a second code path.
- Guardrail #4 (tracing never breaks a session): unaffected - every SDK call
  already goes through `Tracing`'s existing try/except swallowing; STT adds
  no new SDK touchpoints.

## Free-tier impact
None on Groq (no new requests). Adds spans to the self-hosted Langfuse
instance, which has no quota.

## Effort estimate
S (<2h): one file (`stt.py`) restructured into a thin traced wrapper plus a
private method, one param threaded through `pipeline.py`, plus two new tests
(`test_voice_stt.py`) asserting a span is recorded on both the success and
`STTError` paths using a fake tracing client, mirroring
`test_llm_provider.py`.

## Verdict
**GO**.
