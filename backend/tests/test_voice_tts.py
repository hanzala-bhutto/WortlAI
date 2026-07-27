"""Contract for the edge-tts synthesizer and its sentence/rate helpers.

edge-tts itself is never called here: a fake Communicate factory stands in, so these
describe our behaviour (yield only audio chunks, map/clamp the rate, split German
into sentences, turn a backend blow-up into a TTSError) without a network or the
edge-tts package.
"""

import pytest

from app.voice.tts import EdgeTTS, TTSError, rate_to_percent, split_sentences


def test_split_sentences_separates_on_terminators():
    assert split_sentences("Hallo. Wie geht's? Gut!") == [
        "Hallo.",
        "Wie geht's?",
        "Gut!",
    ]


def test_split_sentences_keeps_a_trailing_fragment():
    assert split_sentences("Ein Satz ohne Punkt") == ["Ein Satz ohne Punkt"]


def test_split_sentences_drops_empty():
    assert split_sentences("   \n  ") == []


@pytest.mark.parametrize(
    "rate,expected",
    [(1.0, "+0%"), (0.7, "-30%"), (1.2, "+20%"), (2.0, "+20%"), (0.1, "-30%")],
)
def test_rate_to_percent_maps_and_clamps_to_the_default_band(rate, expected):
    assert rate_to_percent(rate) == expected


def test_rate_to_percent_honors_a_configured_band():
    # A wider operator-set band clamps differently: 1.5 is allowed, 3.0 still capped.
    assert rate_to_percent(1.5, rate_min=0.5, rate_max=2.0) == "+50%"
    assert rate_to_percent(3.0, rate_min=0.5, rate_max=2.0) == "+100%"
    assert rate_to_percent(0.2, rate_min=0.5, rate_max=2.0) == "-50%"


class _FakeComm:
    def __init__(self, frames):
        self._frames = frames

    async def stream(self):
        for frame in self._frames:
            yield frame


async def test_synth_yields_only_non_empty_audio_frames():
    frames = [
        {"type": "audio", "data": b"AAA"},
        {"type": "WordBoundary", "offset": 0},  # not audio -> skipped
        {"type": "audio", "data": b"BBB"},
        {"type": "audio", "data": b""},  # empty audio -> skipped
    ]
    captured = {}

    def factory(*, text, voice, rate):
        captured.update(text=text, voice=voice, rate=rate)
        return _FakeComm(frames)

    tts = EdgeTTS(voice="de-DE-KatjaNeural", communicate_factory=factory)
    out = [chunk async for chunk in tts.synth("Hallo.", rate=0.7)]

    assert out == [b"AAA", b"BBB"]
    assert captured == {"text": "Hallo.", "voice": "de-DE-KatjaNeural", "rate": "-30%"}


async def test_synth_of_blank_text_yields_nothing_and_never_calls_the_backend():
    def factory(**_kwargs):
        raise AssertionError("must not synthesise empty text")

    tts = EdgeTTS(voice="v", communicate_factory=factory)
    assert [chunk async for chunk in tts.synth("   ")] == []


async def test_synth_wraps_a_backend_failure_as_ttserror():
    class _Boom:
        async def stream(self):
            raise RuntimeError("edge-tts endpoint gone")
            yield  # pragma: no cover - makes this an async generator

    tts = EdgeTTS(voice="v", communicate_factory=lambda **_k: _Boom())
    with pytest.raises(TTSError):
        [chunk async for chunk in tts.synth("Hallo.")]
