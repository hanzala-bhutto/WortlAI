"""Contract for the Tutor agent v1.

Two things matter and are hard to eyeball in production, so they are pinned here:

- The German-only guardrail (guardrail #2): a reply that drifts into English is
  regenerated once before anyone sees it - validate-then-stream, so no half-English
  turn ever reaches the learner. Level is pinned by the prompt; correctness of the
  level is the evals' job (#... Phase 3), not this unit test.
- Message assembly: the Tutor's instructions come from the prompt store (system
  persona + few-shot), and the live conversation history is appended after them,
  never folded into the versioned prompt.

Everything runs against a fake completer and the real offline PromptStore, so no
network is touched and the behaviour described is ours, not the model's.
"""

from types import SimpleNamespace

import pytest

from app.agents.scenarios import get_scenario
from app.agents.tutor import Tutor, english_drift, reply_is_acceptable
from app.llmops.prompts import PromptStore


class FakeCompleter:
    """Stands in for LLMProvider.complete: hands back queued replies in order and
    records how it was called (messages + temperature), so the regeneration path
    can be asserted."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    async def complete(self, messages, *, temperature=0.7, max_tokens=None):
        self.calls.append(
            SimpleNamespace(
                messages=messages, temperature=temperature, max_tokens=max_tokens
            )
        )
        return self._replies.pop(0)


def make_store(tmp_path):
    """Offline PromptStore over a bundled tutor-system chat prompt whose system
    message echoes the variables, so tests can assert they were substituted."""
    (tmp_path / "tutor-system.chat.json").write_text(
        '[{"role": "system", "content": '
        '"Rolle: {{persona}} Niveau: {{level}} Titel: {{scenario_title}} '
        'Redemittel: {{redemittel}}. Antworte nur auf Deutsch."}]',
        encoding="utf-8",
    )
    return PromptStore(client=None, fallback_dir=tmp_path)


def make_tutor(tmp_path, replies):
    provider = FakeCompleter(replies)
    tutor = Tutor(provider=provider, prompt_store=make_store(tmp_path))
    return tutor, provider


async def test_reply_prepends_system_prompt_then_history(tmp_path):
    tutor, provider = make_tutor(tmp_path, ["Guten Morgen! Was möchten Sie?"])
    scenario = get_scenario("baeckerei")
    history = [{"role": "user", "content": "Hallo"}]

    result = await tutor.reply(scenario=scenario, level="A2", history=history)

    assert result.text == "Guten Morgen! Was möchten Sie?"
    assert result.regenerated is False
    sent = provider.calls[0].messages
    assert sent[0]["role"] == "system"
    assert sent[-1] == {"role": "user", "content": "Hallo"}
    # Scenario data reached the prompt as variables, not as an inlined string.
    assert scenario.persona in sent[0]["content"]
    assert "A2" in sent[0]["content"]


async def test_reply_regenerates_once_on_english_drift(tmp_path):
    tutor, provider = make_tutor(
        tmp_path,
        ["Sure, I can help you with that!", "Natürlich, wie kann ich helfen?"],
    )
    scenario = get_scenario("cafe")

    result = await tutor.reply(scenario=scenario, level="A2", history=[])

    assert result.text == "Natürlich, wie kann ich helfen?"
    assert result.regenerated is True
    # The retry is deterministic (temperature 0) so a strong prompt can win.
    assert provider.calls[1].temperature == 0.0
    assert len(provider.calls) == 2


async def test_reply_does_not_regenerate_a_clean_german_reply(tmp_path):
    tutor, provider = make_tutor(tmp_path, ["Ja, gern. Was darf es sein?"])
    scenario = get_scenario("baeckerei")

    result = await tutor.reply(scenario=scenario, level="A2", history=[])

    assert result.regenerated is False
    assert len(provider.calls) == 1


async def test_reply_streams_validated_text_in_chunks(tmp_path):
    tutor, _ = make_tutor(tmp_path, ["Guten Tag, wie geht es Ihnen?"])
    scenario = get_scenario("nachbar")
    chunks = []

    result = await tutor.reply(
        scenario=scenario, level="A2", history=[], writer=chunks.append
    )

    # The client sees progressive chunks, and they reconstruct the exact reply.
    assert len(chunks) > 1
    assert "".join(chunks) == result.text == "Guten Tag, wie geht es Ihnen?"


async def test_streamed_text_is_only_the_validated_reply(tmp_path):
    # The English first draft must never be streamed; only the regenerated German.
    tutor, _ = make_tutor(
        tmp_path, ["Hello, how are you?", "Hallo, wie geht es Ihnen?"]
    )
    scenario = get_scenario("nachbar")
    chunks = []

    await tutor.reply(scenario=scenario, level="A2", history=[], writer=chunks.append)

    streamed = "".join(chunks)
    assert streamed == "Hallo, wie geht es Ihnen?"
    assert "Hello" not in streamed


def test_english_drift_flags_english_and_passes_german():
    assert english_drift("Sure, you can pay at the counter please") is True
    assert english_drift("Ja, Sie können an der Kasse bezahlen.") is False
    # A German reply that happens to name a loanword is not drift on its own.
    assert english_drift("Ich nehme einen Cappuccino, bitte.") is False


def test_reply_is_acceptable_rejects_empty():
    ok, reason = reply_is_acceptable("   ")
    assert ok is False
    assert reason == "empty"
