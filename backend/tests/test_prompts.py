"""Contract for the prompt store.

The store is the one place agents get their instructions from. The behaviour that
matters, and that decides whether a session survives a Langfuse outage, is the
three-tier resolve: live prompt when Langfuse answers, the SDK's cached/fallback
copy when it does not, and - when Langfuse is not configured at all - the bundled
in-repo copy with no network touched. A prompt with no bundled fallback is a bug,
not a runtime surprise, so it fails loudly.

Everything runs against a fake Langfuse client, so these tests never hit the
network and describe our wrapper's behaviour, not the SDK's.
"""

import json
from types import SimpleNamespace

import pytest

from app.llmops.prompts import PromptStore


def write_fallback(directory, name, text):
    (directory / f"{name}.txt").write_text(text, encoding="utf-8")


def write_chat_fallback(directory, name, messages):
    (directory / f"{name}.chat.json").write_text(
        json.dumps(messages), encoding="utf-8"
    )


class FakePrompt:
    """Stands in for a langfuse TextPromptClient: compiles {{var}} and reports
    whether it is the SDK's own fallback and which version it is."""

    def __init__(self, text, *, is_fallback=False, version=3):
        self._text = text
        self.is_fallback = is_fallback
        self.version = version

    def compile(self, **variables):
        out = self._text
        for key, value in variables.items():
            out = out.replace("{{" + key + "}}", str(value))
            out = out.replace("{{ " + key + " }}", str(value))
        return out


class FakeClient:
    def __init__(self, *, prompt=None, error=None):
        self._prompt = prompt
        self._error = error
        self.calls = []

    def get_prompt(self, name, *, label, type, fallback):
        self.calls.append(
            SimpleNamespace(name=name, label=label, type=type, fallback=fallback)
        )
        if self._error is not None:
            raise self._error
        return self._prompt


def test_live_prompt_is_used_when_langfuse_returns_one(tmp_path):
    write_fallback(tmp_path, "tutor", "BUNDLED {{level}}")
    client = FakeClient(prompt=FakePrompt("Reply in German at {{level}}", version=5))
    store = PromptStore(client=client, fallback_dir=tmp_path)

    result = store.get("tutor", variables={"level": "A2"})

    assert result.text == "Reply in German at A2"
    assert result.is_fallback is False
    assert result.version == 5
    # The bundled copy is still handed to the SDK so its own guaranteed-availability
    # path has something to serve if the live fetch fails inside the SDK.
    assert client.calls[0].fallback == "BUNDLED {{level}}"
    assert client.calls[0].label == "production"


def test_unconfigured_store_uses_bundled_fallback_without_network(tmp_path):
    write_fallback(tmp_path, "tutor", "Sprich Deutsch, Niveau {{level}}")
    store = PromptStore(client=None, fallback_dir=tmp_path)

    result = store.get("tutor", variables={"level": "B1"})

    assert result.text == "Sprich Deutsch, Niveau B1"
    assert result.is_fallback is True
    assert result.version is None


def test_sdk_error_degrades_to_bundled_fallback(tmp_path):
    write_fallback(tmp_path, "tutor", "FALLBACK {{level}}")
    client = FakeClient(error=RuntimeError("langfuse unreachable"))
    store = PromptStore(client=client, fallback_dir=tmp_path)

    result = store.get("tutor", variables={"level": "A2"})

    assert result.text == "FALLBACK A2"
    assert result.is_fallback is True


def test_missing_bundled_fallback_is_a_hard_error(tmp_path):
    store = PromptStore(client=None, fallback_dir=tmp_path)

    with pytest.raises(KeyError):
        store.get("no-such-prompt")


def test_label_passes_through_to_the_sdk(tmp_path):
    write_fallback(tmp_path, "tutor", "x")
    client = FakeClient(prompt=FakePrompt("y"))
    store = PromptStore(client=client, fallback_dir=tmp_path)

    store.get("tutor", label="staging")

    assert client.calls[0].label == "staging"
    assert client.calls[0].type == "text"


class FakeChatPrompt:
    """Stands in for a langfuse ChatPromptClient: compiles {{var}} inside each
    message's content and returns a list of {role, content} dicts."""

    def __init__(self, messages, *, is_fallback=False, version=4):
        self._messages = messages
        self.is_fallback = is_fallback
        self.version = version

    def compile(self, **variables):
        out = []
        for msg in self._messages:
            content = msg["content"]
            for key, value in variables.items():
                content = content.replace("{{" + key + "}}", str(value))
                content = content.replace("{{ " + key + " }}", str(value))
            out.append({"role": msg["role"], "content": content})
        return out


def test_live_chat_prompt_is_used_when_langfuse_returns_one(tmp_path):
    write_chat_fallback(tmp_path, "tutor-system", [{"role": "system", "content": "BUNDLED"}])
    client = FakeClient(
        prompt=FakeChatPrompt(
            [{"role": "system", "content": "Sprich Deutsch, Niveau {{level}}"}],
            version=7,
        )
    )
    store = PromptStore(client=client, fallback_dir=tmp_path)

    result = store.get_chat("tutor-system", variables={"level": "A2"})

    assert result.messages == [{"role": "system", "content": "Sprich Deutsch, Niveau A2"}]
    assert result.is_fallback is False
    assert result.version == 7
    assert client.calls[0].type == "chat"


def test_unconfigured_chat_store_renders_bundled_fallback(tmp_path):
    write_chat_fallback(
        tmp_path,
        "tutor-system",
        [{"role": "system", "content": "Niveau {{level}}, Rolle {{persona}}"}],
    )
    store = PromptStore(client=None, fallback_dir=tmp_path)

    result = store.get_chat(
        "tutor-system", variables={"level": "B1", "persona": "Bäcker"}
    )

    assert result.messages == [{"role": "system", "content": "Niveau B1, Rolle Bäcker"}]
    assert result.is_fallback is True
    assert result.version is None


def test_chat_sdk_error_degrades_to_bundled_fallback(tmp_path):
    write_chat_fallback(tmp_path, "tutor-system", [{"role": "system", "content": "FB {{level}}"}])
    client = FakeClient(error=RuntimeError("langfuse unreachable"))
    store = PromptStore(client=client, fallback_dir=tmp_path)

    result = store.get_chat("tutor-system", variables={"level": "A2"})

    assert result.messages == [{"role": "system", "content": "FB A2"}]
    assert result.is_fallback is True


def test_missing_chat_fallback_is_a_hard_error(tmp_path):
    store = PromptStore(client=None, fallback_dir=tmp_path)

    with pytest.raises(KeyError):
        store.get_chat("no-such-chat-prompt")


def test_malformed_chat_fallback_is_a_hard_error(tmp_path):
    (tmp_path / "tutor-system.chat.json").write_text(
        json.dumps([{"role": "system"}]), encoding="utf-8"  # missing content
    )
    store = PromptStore(client=None, fallback_dir=tmp_path)

    with pytest.raises(ValueError):
        store.get_chat("tutor-system")
