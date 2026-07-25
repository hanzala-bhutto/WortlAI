# Feasibility: 006 - Talk page UI

- **Issue**: #8 · **Phase**: 1 · **Date**: 2026-07-25 · **Author**: Claude (reviewed by Hanzala)

## Goal
The daily-driver screen: push-to-talk, streaming transcript with tap-gloss (German-only default), audio autoplay, rate slider, "Wie bitte?", debrief view, hours tracking.

## Approach options
1. **Single Next.js route with a `useVoiceSession` hook owning the WS + MediaRecorder + audio queue (chosen)** - one stateful hook, dumb components; easiest to debug.
2. State library (Zustand/Redux) - unnecessary for one screen; add later if state sprawls.

## Risks & unknowns
- Browser autoplay policies block un-gestured audio → first user gesture (the mic press) unlocks an AudioContext; standard pattern.
- MediaRecorder codec differences (Chrome webm/opus vs others) → target Chrome for MVP, note in README.
- Audio queue discipline (don't overlap TTS sentences) → simple FIFO player in the hook.

## Free-tier impact
None directly.

## Effort estimate
L (1–2 days) - audio UX polish is where frontend time goes.

## Verdict
**GO** - Chrome-only for MVP is an accepted constraint.
