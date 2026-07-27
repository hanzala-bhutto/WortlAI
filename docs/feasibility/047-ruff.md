# Feasibility: 047 - ruff lint + format across backend

- **Issue**: #47
- **Phase / Milestone**: Tooling (no phase deliverable)
- **Date**: 2026-07-27
- **Author**: Claude (reviewed by Hanzala)

## Goal
Bring `ruff` (linter + formatter) to `backend/` so dead imports, obvious bugs and
style drift are caught before review instead of by eye. Today nothing lints the code
(no ruff/flake8/mypy in requirements, no pyproject, no CI). Outcome: one config, a
pre-commit hook that runs on every commit, and a clean `ruff check` / `ruff format
--check` baseline.

## Approach options
1. **Lint + format the whole tree, core rules, pre-commit gate (chosen)** - one
   formatting reflow up front, then the tree stays clean for free. Rule set is
   `E, F, I` (pyflakes + import sorting) plus `B` (bugbear), `UP` (pyupgrade), `SIM`:
   real bugs and modernisation, not pedantry. Enforced by `.pre-commit-config.yaml`;
   no CI exists yet, so the local hook is the gate.
2. **Lint only, no formatter** - smaller first diff, but style stays inconsistent and
   we relitigate wrapping in every review. Rejected.
3. **Aggressive rules (`ANN`, `D`, `PL`)** - annotation/docstring enforcement floods
   the first pass with hundreds of findings and stalls the adoption. Defer; can add
   families later once the baseline is green.

## Risks & unknowns
- **Formatter vs. E501 tug-of-war** → set `line-length = 88` and let the formatter own
  wrapping; ignore `E501` so unsplittable long strings (URLs, prompts) don't nag.
- **Reflow buries logic in review** → split into three commits: config+hook+docs, a
  pure `ruff format` reflow (no logic change), then hand-reviewed `check --fix`. Review
  can skip the middle commit.
- **`B`/`SIM` flag deliberate patterns** (e.g. the collector's broad `except Exception`
  that is guardrail #4) → suppress with a targeted `# noqa: BLE001` + comment, never
  "fix" a deliberate degrade path.
- **Line-length reflow touches ~30 lines** in `app/` (5 over 100 cols) → expected and
  contained to the format commit.

## Free-tier impact
None. Local dev tooling only; touches no Groq/Whisper/NIM/Langfuse quota.

## Effort estimate
S (<2h). Main cost is hand-reviewing the `check --fix` findings and confirming the
reflow changes no behaviour (pytest stays green).

## Verdict
**GO** - low risk, self-contained, pays for itself on the next PR.
