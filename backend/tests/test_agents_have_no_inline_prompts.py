"""Guardrail: no prompt strings hardcoded in agent code.

CLAUDE.md and issue #7 require every prompt to come from the prompt store (so it
is versioned in Langfuse and editable without a redeploy). This test fails the
build if someone pastes a system prompt straight into an agent module: it scans
app/agents/ for long (> 200 char) string literals that are not docstrings.

What this is and is NOT:
- It is a *heuristic tripwire*, not a proof. It cannot tell a prompt from any
  other long string. A long non-prompt string in an agent would be a false
  positive (fix: refactor it out or shorten it); a prompt under 200 chars pasted
  inline would slip through. Both are rare, and the point is to catch the obvious
  "I'll just inline this system prompt" mistake, not to be airtight.
- It says nothing about whether a prompt is *correct* - that is the evals' job
  (Corrector precision/recall, Tutor LLM-as-judge), a separate layer entirely.
- It is scoped to app/agents/ because that is the only place the rule applies.

Bundled fallback prompts are exempt by construction: they live as .txt files under
app/llmops/fallbacks/, not in .py files, so they are never scanned.
"""

import ast
from pathlib import Path

from app.config import BACKEND_DIR

AGENTS_DIR = BACKEND_DIR / "app" / "agents"

# A real prompt is long; incidental strings (log messages, keys) are short.
MAX_INLINE_STRING = 200


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """id() of every string Constant that is a module/class/function docstring, so
    those are not mistaken for inline prompts."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return docstrings


def find_inline_prompts(root: Path) -> list[str]:
    """Return 'path:line' for every long, non-docstring string literal under root."""
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and len(node.value) > MAX_INLINE_STRING
                and id(node) not in docstrings
            ):
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    return offenders


def test_no_inline_prompts_in_agents():
    assert find_inline_prompts(AGENTS_DIR) == []


def test_finder_flags_a_hardcoded_prompt(tmp_path):
    bad = tmp_path / "tutor.py"
    prompt = "You are a German tutor. " + "Reply only in German. " * 20
    bad.write_text(f'SYSTEM = "{prompt}"\n', encoding="utf-8")

    offenders = find_inline_prompts(tmp_path)

    assert len(offenders) == 1
    assert offenders[0].startswith("tutor.py:")


def test_finder_ignores_docstrings(tmp_path):
    ok = tmp_path / "mod.py"
    long_doc = "A module. " + "This is documentation, not a prompt. " * 20
    ok.write_text(f'"""{long_doc}"""\n\nx = 1\n', encoding="utf-8")

    assert find_inline_prompts(tmp_path) == []
