"""Local Cantonese adapter: no voice substitution, bounded PCM and cancellation."""

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest


def test_speaker_routes_and_cancels_cantonese_without_touching_english():
    from alice.conversation.audio import Speaker

    async def run():
        entered = asyncio.Event()
        english_calls, cantonese_calls, cancellations, events = [], [], [], []

        class English:
            async def stream(self, clause):
                english_calls.append(clause.text)
                yield SimpleNamespace(pcm=np.zeros(24, np.float32), sample_rate=24000)

            async def cancel(self, generation):
                raise AssertionError("wrong voice cancellation")

        class Cantonese:
            async def stream(self, clause):
                cantonese_calls.append(clause.text)
                entered.set()
                await asyncio.Event().wait()
                if False:
                    yield None

            async def cancel(self, generation):
                cancellations.append(generation)

        speaker = Speaker(lambda stage, state, **d: events.append((stage, state, d)))
        speaker.worker = English()
        speaker.cantonese_worker = Cantonese()
        task = asyncio.create_task(
            speaker.speak("你好呀。", "turn-yue", audible=False, language="Cantonese")
        )
        await asyncio.wait_for(entered.wait(), 1)
        await speaker.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await speaker.speak("Hello.", "turn-en", audible=False, language="English")
        assert cantonese_calls == ["你好呀。"]
        assert english_calls == ["Hello."]
        assert cancellations == ["turn-yue"]
        assert any(d.get("language") == "Cantonese" for s, _, d in events if s == "tts")

    asyncio.run(run())


def test_missing_cantonese_voice_fails_before_english_synthesis():
    from alice.conversation.audio import Speaker

    async def run():
        speaker = Speaker(lambda *args, **kwargs: None)
        with pytest.raises(RuntimeError, match="Cantonese voice"):
            await speaker.speak("你好呀。", "turn", audible=False, language="Cantonese")

    asyncio.run(run())


def test_quiet_cantonese_is_raised_to_speech_level_with_peak_and_gain_limits():
    from alice.conversation.cantonese import normalize_speech

    quiet = np.array([-0.025, 0.025], np.float32)
    adjusted = normalize_speech(quiet)
    np.testing.assert_allclose(adjusted, [-0.12, 0.12], rtol=1e-6)
    peaky = np.zeros(1000, np.float32)
    peaky[0] = 0.8
    assert np.max(np.abs(normalize_speech(peaky))) == pytest.approx(0.95)
    tiny = np.array([0.001, -0.001], np.float32)
    np.testing.assert_allclose(normalize_speech(tiny), [0.008, -0.008], rtol=1e-6)
    np.testing.assert_array_equal(normalize_speech(np.zeros(10, np.float32)), 0)
