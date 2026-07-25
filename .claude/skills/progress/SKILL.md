---
name: progress
description: Generate Hanzala's German learning progress report from the WortlAI learner DB - immersion hours vs the 2-month protocol target, FSRS due counts, error-type trends, CEFR trajectory. Use when asked "how am I doing" or for the weekly review.
---

# /progress - learner progress report

**Status: learner DB lands in Phase 1–2.** Until `backend/data/wortlai.db` has session data, report that and stop.

## Queries (read-only, SQLite)

1. **Hours**: total immersion hours (app sessions + logged call segments + missions) per day for last 14 days; cumulative vs the 2-month protocol line (target ~3 hrs/day ≈ 180h total). State plainly the projected finish date at the current pace.
2. **Streak**: consecutive days with ≥1 session.
3. **FSRS**: due today / overdue / total tracked chunks; retention estimate (share graded Good/Easy in last 7 days).
4. **Errors**: top 5 error types by frequency, each with 14-day trend (rising/falling) and one real example utterance.
5. **Level**: latest Assessment-agent CEFR estimate + history (Phase 3; skip gracefully before that).
6. **Vocab coverage**: known lemmas vs Goethe A2/B1 lists in % (Phase 2+).

## Report

Short prose summary first (the "how am I actually doing" answer - honest: if pace implies 6 months, say so). Then compact tables. Close with the single highest-leverage recommendation for next week (e.g. "your Perfekt-auxiliary errors aren't falling - request a scenario focused on past-tense storytelling").
