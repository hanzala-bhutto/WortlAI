"""Where agents get their prompts. Never hardcode a prompt in an agent - fetch it
here (a build check enforces this: tests/test_no_hardcoded_prompts.py).

Three-tier resolve, so a prompt is always available:
1. Langfuse, by name + label ('production' / 'staging'). Editing it there changes
   behaviour with no redeploy. The SDK caches client-side (60s TTL, revalidated in
   the background), so after the first fetch this adds no latency to a turn.
2. The SDK's own cache / fallback: when Langfuse is briefly unreachable, the SDK
   serves the last-known version, or the bundled copy we hand it as `fallback`.
3. When Langfuse is not configured at all, we skip the SDK entirely and render the
   bundled copy ourselves - no network touched.

Every prompt MUST have a bundled fallback file at fallbacks/<name>.txt. A prompt
without one is a packaging bug and raises, rather than failing mid-session.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.llmops.client import get_langfuse

logger = logging.getLogger(__name__)

FALLBACK_DIR = Path(__file__).parent / "fallbacks"

# Matches a mustache placeholder like `{{ level }}`: `{{`, optional spaces, the
# variable name (captured), optional spaces, `}}`. Used only on the offline path.
_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _render(template: str, variables: dict[str, object]) -> str:
    """Minimal {{var}} substitution for the offline path. Langfuse does this
    itself when it serves the prompt; this only runs when we render a bundled
    fallback ourselves. Unknown placeholders are left intact, not blanked."""
    return _VAR.sub(
        lambda m: str(variables[m.group(1)]) if m.group(1) in variables else m.group(0),
        template,
    )


@dataclass(frozen=True)
class RenderedPrompt:
    text: str
    is_fallback: bool  # True when the bundled copy was used, not the live version
    name: str
    version: int | None  # the Langfuse version, or None for a bundled fallback


class PromptStore:
    """Fetches prompts by name. Pass `client=None` to force the offline path;
    otherwise pass a Langfuse client (build_prompt_store() wires the shared one)."""

    def __init__(self, client=None, *, fallback_dir: Path = FALLBACK_DIR) -> None:
        self._client = client
        self._dir = fallback_dir

    def _fallback_text(self, name: str) -> str:
        path = self._dir / f"{name}.txt"
        if not path.exists():
            raise KeyError(
                f"No bundled fallback prompt for {name!r} at {path}. Every prompt "
                f"needs a fallback file so a session survives a Langfuse outage."
            )
        return path.read_text(encoding="utf-8")

    def get(
        self,
        name: str,
        *,
        label: str = "production",
        variables: dict[str, object] | None = None,
    ) -> RenderedPrompt:
        variables = variables or {}
        fallback = self._fallback_text(name)  # required; raises if missing

        if self._client is None:
            return RenderedPrompt(_render(fallback, variables), True, name, None)

        try:
            prompt = self._client.get_prompt(
                name, label=label, type="text", fallback=fallback
            )
            is_fallback = bool(getattr(prompt, "is_fallback", False))
            # A fallback has no meaningful Langfuse version (the SDK reports 0);
            # normalise to None so "is_fallback => no version" holds on both paths.
            version = None if is_fallback else getattr(prompt, "version", None)
            return RenderedPrompt(
                prompt.compile(**variables), is_fallback, name, version
            )
        except Exception as exc:  # the SDK should not raise once given a fallback,
            # but a session must never die on a prompt fetch - render ours instead.
            logger.warning(
                "Prompt fetch failed for %r, using bundled fallback: %s", name, exc
            )
            return RenderedPrompt(_render(fallback, variables), True, name, None)


def build_prompt_store() -> PromptStore:
    """The app-wide prompt store over the shared Langfuse client (or offline)."""
    return PromptStore(client=get_langfuse())
