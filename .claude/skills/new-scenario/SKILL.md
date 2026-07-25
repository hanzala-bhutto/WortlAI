---
name: new-scenario
description: Scaffold a new WortlAI roleplay scenario end-to-end - system prompt, Redemittel/vocab links, level calibration, registry entry. Use when adding a conversation scenario (e.g. "Termin beim Arzt", "Wohnungsbesichtigung").
---

# /new-scenario - scaffold a roleplay scenario

**Status: scenario registry lands in Phase 1.** Until `backend/app/agents/scenarios/` exists, report that and stop.

## Inputs (ask if not given)

- Scenario name (German), goal with stakes (what does "success" mean - e.g. "get the Termin"), CEFR level range, tutor persona (friendly Bäckerin? grumpy Beamter? which TTS voice).

## Steps

1. Create `backend/app/agents/scenarios/<slug>.py` from the existing scenario template: persona description, scene setup, success/fail conditions, level calibration notes.
2. Draft the scenario system prompt **in Langfuse** (staging label), following the house rules: reply ONLY in German at level {X}; weave in the session brief's due vocab; never correct mid-conversation.
3. Query Qdrant `content` for matching Redemittel sets and link the top chunks into the scenario's vocab hints. If Phase 2 stores aren't built yet, leave a `TODO(phase-2)` marker with the intended query.
4. Register in the scenario registry; add one happy-path test (scenario loads, prompt renders, success condition parses).

## Report

Scenario file path, Langfuse prompt name+version, linked chunk count, and a 3-line sample opening the Tutor would say.
