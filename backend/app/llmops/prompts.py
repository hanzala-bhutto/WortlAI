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

from __future__ import annotations

import json
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


def _render_messages(
    messages: list[ChatMessage], variables: dict[str, object]
) -> list[ChatMessage]:
    """Substitute {{var}} in each message's content on the offline path. Roles are
    left untouched; only content carries placeholders."""
    return [
        {**msg, "content": _render(msg.get("content", ""), variables)}
        for msg in messages
    ]


def _coerce_messages(compiled: object) -> list[ChatMessage]:
    """Normalise a compiled chat prompt down to the {role, content} pairs the
    provider consumes. Langfuse may attach extra keys or placeholder markers; we
    keep only messages that actually carry a role and content."""
    out: list[ChatMessage] = []
    for msg in compiled or []:  # type: ignore[union-attr]
        if isinstance(msg, dict) and "role" in msg and "content" in msg:
            out.append({"role": str(msg["role"]), "content": str(msg["content"])})
    return out


@dataclass(frozen=True)
class RenderedPrompt:
    text: str
    is_fallback: bool  # True when the bundled copy was used, not the live version
    name: str
    version: int | None  # the Langfuse version, or None for a bundled fallback


# One chat message, the OpenAI shape the provider already speaks. The Tutor's
# versioned prompt is its leading messages (system persona + a few German-only
# few-shot turns); the live conversation history is appended at runtime, not
# stored in the prompt.
ChatMessage = dict[str, str]


@dataclass(frozen=True)
class RenderedChatPrompt:
    messages: list[ChatMessage]
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

    def _fallback_chat(self, name: str) -> list[ChatMessage]:
        """The bundled chat prompt: a JSON array of {role, content} messages at
        fallbacks/<name>.chat.json. Same contract as the text fallback - missing
        or malformed is a packaging bug that fails loudly, not mid-session."""
        path = self._dir / f"{name}.chat.json"
        if not path.exists():
            raise KeyError(
                f"No bundled chat fallback for {name!r} at {path}. Every prompt "
                f"needs a fallback file so a session survives a Langfuse outage."
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not all(
            isinstance(m, dict) and "role" in m and "content" in m for m in data
        ):
            raise ValueError(
                f"Bundled chat fallback {path} must be a JSON array of "
                f"{{'role', 'content'}} objects."
            )
        return [{"role": str(m["role"]), "content": str(m["content"])} for m in data]

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

    def get_chat(
        self,
        name: str,
        *,
        label: str = "production",
        variables: dict[str, object] | None = None,
    ) -> RenderedChatPrompt:
        """A versioned chat prompt (list of messages), resolved through the same
        three tiers as get(): live Langfuse, the SDK's cache/fallback, or the
        bundled copy when Langfuse is unconfigured. The Tutor's system persona and
        few-shot turns live here; runtime conversation history is appended by the
        caller, not stored in the prompt."""
        variables = variables or {}
        fallback = self._fallback_chat(name)  # required; raises if missing

        if self._client is None:
            return RenderedChatPrompt(
                _render_messages(fallback, variables), True, name, None
            )

        try:
            prompt = self._client.get_prompt(
                name, label=label, type="chat", fallback=fallback
            )
            is_fallback = bool(getattr(prompt, "is_fallback", False))
            version = None if is_fallback else getattr(prompt, "version", None)
            messages = _coerce_messages(prompt.compile(**variables))
            return RenderedChatPrompt(messages, is_fallback, name, version)
        except Exception as exc:  # a session must never die on a prompt fetch.
            logger.warning(
                "Chat prompt fetch failed for %r, using bundled fallback: %s",
                name,
                exc,
            )
            return RenderedChatPrompt(
                _render_messages(fallback, variables), True, name, None
            )


def build_prompt_store() -> PromptStore:
    """The app-wide prompt store over the shared Langfuse client (or offline)."""
    return PromptStore(client=get_langfuse())
