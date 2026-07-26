#!/usr/bin/env bash
# PreToolUse guard for `gh pr create`.
#
# Scoped by the hook's `if: Bash(gh pr create*)`, so it only runs when a PR is
# being created. If any required metadata flag is absent, it asks for
# confirmation and reminds Claude of the pr-issue-complete-info rule: every PR
# needs labels (phase:N + area:*), a milestone, the WortlAI project, an assignee,
# and a body with `Closes #N`, mirroring the source issue's labels/milestone/project.
#
# No jq dependency: the command string lives in the raw stdin JSON, so we grep
# the raw input for each flag rather than parsing.
input=$(cat)
missing=""
for flag in --label --milestone --assignee --project; do
  printf '%s' "$input" | grep -q -- "$flag" || missing="$missing $flag"
done
if [ -n "$missing" ]; then
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"PR metadata incomplete (missing:%s). On gh pr create set: --label phase:N and --label area:X, --milestone, --project WortlAI, --assignee hanzala-bhutto, plus a body containing Closes #N. Mirror the source issue labels/milestone/project (pr-issue-complete-info)."}}' "$missing"
fi
