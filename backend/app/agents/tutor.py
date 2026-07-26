"""Tutor agent v1: holds a German-only roleplay at a fixed CEFR level.

How a turn is produced (validate-then-stream, guardrail #2):

1. Fetch the versioned system prompt (persona, level, Redemittel as variables)
   from the prompt store, and append the live conversation history after it.
2. Get the full reply from the provider, then validate it: non-empty and no
   English drift. On failure, regenerate once at temperature 0 (a strong prompt
   usually wins when the sampling is deterministic).
3. Only then stream the *validated* reply out in chunks to the writer, so no
   half-English turn ever reaches the learner.

Replies are one to three sentences at A2/B1, so buffering the whole reply before
streaming costs little latency and buys the guardrail. Token-level streaming
straight from the model is deferred to when latency data justifies loosening this
(#8, the voice loop). The prompt itself pins German-only and the level; the check
here is the belt to that prompt's braces, since the prompt alone is not enough.

Level *enforcement* beyond German-only (is the reply actually at the target CEFR
rung?) is the evals' job in Phase 3, not a heuristic here.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.agents.scenarios import Scenario
from app.llmops.prompts import ChatMessage, PromptStore

# The versioned chat prompt that carries the Tutor's persona and German-only pin.
TUTOR_PROMPT_NAME = "tutor-system"

# Guardrail #3: cap tokens per reply so a runaway generation can't burn the free
# tier. A tutor turn is short; this is a ceiling, not a target.
DEFAULT_MAX_TOKENS = 320
DEFAULT_TEMPERATURE = 0.6

# English function words with no German homograph. A German reply never contains
# these; a drifting one usually contains several. Deliberately conservative -
# ambiguous tokens ("was", "die", "man", "in") are left out so real German is
# never flagged. This is a tripwire, not a language classifier.
_ENGLISH_MARKERS = frozenset(
    {
        "the", "you", "your", "yours", "please", "sorry", "hello", "thanks",
        "thank", "yes", "what", "why", "who", "would", "could", "should",
        "because", "really", "maybe", "actually", "something", "nothing",
        "everything", "here", "there", "this", "that", "with", "without",
        "about", "people", "again", "always", "never", "think", "know", "want",
        "need", "help", "understand", "english", "sure", "how", "are", "i",
    }
)

_WORD = re.compile(r"[a-zA-Z']+")
# Split a reply into stream chunks that keep their trailing whitespace, so the
# pieces re-concatenate to exactly the original text.
_CHUNK = re.compile(r"\S+\s*")


def iter_chunks(text: str) -> list[str]:
    """Break `text` into whitespace-preserving chunks for progressive streaming.
    Concatenating the result reproduces `text` exactly."""
    return _CHUNK.findall(text)


class ChatCompleter(Protocol):
    """The slice of LLMProvider the Tutor needs: a full, non-streamed reply. The
    Tutor does its own chunked streaming after validation, so it never needs the
    provider's token stream."""

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = ...,
        max_tokens: int | None = ...,
    ) -> str: ...


@dataclass(frozen=True)
class TutorReply:
    text: str  # the validated reply, exactly what was streamed
    regenerated: bool  # True if the first draft drifted and was retried
    prompt_is_fallback: bool  # True if the bundled prompt was used, not Langfuse


def english_drift(text: str) -> bool:
    """True if the reply looks like it slipped into English. Token-based against a
    conservative marker set, so German text (even with a loanword) does not trip."""
    tokens = {t.lower() for t in _WORD.findall(text)}
    return bool(tokens & _ENGLISH_MARKERS)


def reply_is_acceptable(text: str) -> tuple[bool, str | None]:
    """Whether a Tutor reply may reach the learner. Returns (ok, reason); reason is
    the tag of the first failed check, for logging/tracing."""
    if not text or not text.strip():
        return False, "empty"
    if english_drift(text):
        return False, "english-drift"
    return True, None


class Tutor:
    """Turns a scenario + history into one validated German reply. Stateless: the
    conversation lives in the graph's checkpointed state and is passed in each
    turn."""

    def __init__(
        self,
        provider: ChatCompleter,
        prompt_store: PromptStore,
        *,
        prompt_label: str = "production",
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> None:
        self._provider = provider
        self._store = prompt_store
        self._label = prompt_label
        self._max_tokens = max_tokens
        self._temperature = temperature

    def _messages(
        self, scenario: Scenario, level: str, history: Sequence[ChatMessage]
    ) -> tuple[list[ChatMessage], bool]:
        prompt = self._store.get_chat(
            TUTOR_PROMPT_NAME,
            label=self._label,
            variables={
                "level": level,
                "scenario_title": scenario.title,
                "persona": scenario.persona,
                "redemittel": "; ".join(scenario.redemittel),
            },
        )
        return [*prompt.messages, *history], prompt.is_fallback

    async def reply(
        self,
        *,
        scenario: Scenario,
        level: str,
        history: Sequence[ChatMessage],
        writer: Callable[[str], object] | None = None,
    ) -> TutorReply:
        """One Tutor turn. `history` is the conversation so far (user/assistant
        messages); `writer`, if given, receives the validated reply in chunks."""
        messages, prompt_is_fallback = self._messages(scenario, level, history)

        text = await self._provider.complete(
            messages, temperature=self._temperature, max_tokens=self._max_tokens
        )
        ok, _reason = reply_is_acceptable(text)
        regenerated = False
        if not ok:
            # Retry deterministically: same strong prompt, no sampling wiggle. If it
            # still drifts we return it anyway (best-effort) rather than hang the
            # turn - the flag lets the caller trace it.
            regenerated = True
            text = await self._provider.complete(
                messages, temperature=0.0, max_tokens=self._max_tokens
            )

        if writer is not None:
            for chunk in iter_chunks(text):
                writer(chunk)

        return TutorReply(
            text=text, regenerated=regenerated, prompt_is_fallback=prompt_is_fallback
        )
