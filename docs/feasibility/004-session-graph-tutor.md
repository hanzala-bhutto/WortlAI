# Feasibility: 004 - LangGraph session graph + Tutor agent v1

- **Issue**: #4 · **Phase**: 1 · **Date**: 2026-07-25 · **Author**: Claude (reviewed by Hanzala)

## Goal
Session lifecycle as a checkpointed LangGraph state graph (setup → converse ⇄ async-correct → debrief) with SQLite persistence; Tutor agent holding German-only scenario conversation at a fixed CEFR level.

## Approach options
1. **LangGraph with SqliteSaver checkpointer (chosen)** - pause/resume across restarts for free; async corrector as a parallel branch; decided in plan after framework comparison.
2. Plain Python state machine - fewer deps but re-implements checkpointing; rejected in planning (user wants LangGraph competence too).

## Risks & unknowns
- LangGraph API churn (fast-moving library) → pin version, isolate graph construction in one module.
- Keeping the Tutor strictly German at level: prompt discipline + few-shot; eval properly in Phase 3, eyeball now.
- Streaming LLM tokens through a graph node to the WS layer needs LangGraph streaming events API → small spike inside the branch.

## Free-tier impact
None beyond provider layer usage.

## Effort estimate
L (1–2 days) - graph + checkpointing + streaming integration.

## Verdict
**GO** - core architecture piece; spike streaming-through-graph first, fall back to tutor-outside-graph pattern if events API fights us.
