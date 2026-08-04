# Experiments

Our own measured evidence, on our own corpus. Not benchmarks from someone's blog.
An experiment doc is a frozen lab-notebook entry: what we ran, the numbers, the finding.

## When to write one (curated - most tasks get none)

Write an experiment doc **only when you resolved a measurable unknown by running something**:
chunk size 256 vs 512 on retrieval quality, `whisper-large` vs `-turbo` WER, top-k 3 vs 8,
embedding model A vs B on our glossary. If there was no number and no comparison, there is no experiment.

Do **not** write one for: bugfixes, refactors, "it works now", or anything you decided from docs
alone (that is a feasibility report, not an experiment).

## Rules

- One file per experiment: `NNN-slug.md`, `NNN` matching the driving issue when there is one.
- **Frozen once written.** An experiment is a dated result. New run, new file - never edit the numbers.
- Show the numbers. A small table or a few lines of measured output beats a paragraph of prose.
- End with what decision it fed, so a reader can follow it forward to the feasibility report or strategy doc.

## How this differs from the neighbours

- **Feasibility** (`docs/feasibility/`) decides *before* an issue, citing evidence. Experiments *produce* that evidence.
- **Strategy** (`docs/strategies/`) records what we run *now*. Experiments are the receipts behind those choices.

Flow: **experiment (measure) -> feasibility (decide) -> strategy (record what we run)**.
