---
name: eval
description: Run the WortlAI eval suite (Corrector precision/recall vs gold dataset, Tutor LLM-as-judge) against current prompt versions, compare to baseline, and report metric deltas. Use after any prompt or agent change, before promoting prompts to production.
---

# /eval - evaluate agent quality vs gold datasets

**Status: harness lands in Phase 3.** Until `backend/app/evals/` exists, report that and stop.

## Steps

1. Confirm Langfuse keys in `.env`. Identify the prompt versions currently labeled `staging` and `production` in Langfuse.
2. Run from `backend/`: `python -m app.evals.run --suite all`
   - **Corrector suite** (deterministic): gold dataset of learner utterances with labeled errors → compute per-error-type precision (hallucinated errors) and recall (missed errors).
   - **Tutor suite** (LLM-as-judge): rubric = CEFR-appropriate? German-only? natural, not textbook-stiff? Judge model MUST differ from (and be stronger than) the model under test.
3. Compare to the last baseline stored in `backend/data/eval_baseline.json`.

## Report

Table: metric | baseline | current | delta. Flag any regression >2pp in red. Verdict line: **promote** (all metrics ≥ baseline) or **hold** (list what regressed and the failing examples' Langfuse trace links).

## Guardrails

- Never promote a prompt to `production` label yourself - report the verdict; Hanzala promotes.
- If the gold dataset has <20 items for a suite, warn that metrics are noise and say how to grow it (triage 👍/👎 feedback).
