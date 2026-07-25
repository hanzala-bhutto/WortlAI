# Feasibility: 003 - Voice pipeline (STT ↔ TTS over WebSocket)

- **Issue**: #3 · **Phase**: 1 · **Date**: 2026-07-25 · **Author**: Claude (reviewed by Hanzala)

## Goal
Mic audio → Groq Whisper transcript → agent reply → edge-tts German audio back, with sentence-level TTS pipelining for <2s to first audio.

## Approach options
1. **Chunked-blob WS protocol (chosen)**: browser MediaRecorder posts complete utterance blobs (push-to-talk = natural utterance boundary); backend runs STT per blob; TTS streamed back per sentence as base64 frames. Simple, robust, matches push-to-talk UX.
2. Continuous audio streaming with server-side VAD - needed for hands-free mode (Phase 3), but adds VAD tuning + partial-transcript complexity now. Defer.

## Risks & unknowns
- **edge-tts is an unofficial endpoint** - could break anytime → interface behind `voice/tts.py`; Qwen3-TTS documented fallback. Accepted risk.
- Whisper on A2-accented German with Urdu/English L1: unknown accuracy → measure on real usage; whisper-large-v3-turbo is near-SOTA, low risk.
- Latency budget: STT ~0.5s (Groq is 200x realtime) + first LLM sentence ~1s + TTS ~0.3s ⇒ <2s achievable.

## Free-tier impact
Whisper: 2,000 audio req/day free; a session ≈ 20 utterances → trivial. edge-tts: no quota.

## Effort estimate
L (1–2 days) - WS protocol + audio plumbing on both ends is the hairiest Phase 1 work.

## Verdict
**GO** - highest-risk Phase 1 item, which is exactly why it's built early.
