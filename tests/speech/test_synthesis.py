import sys
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from alice.speech.synthesis import PocketSynthesizer


@pytest.fixture(autouse=True)
def installed_model_metadata(monkeypatch):
    # Fake model tests must run in the base environment without the speech extra.
    monkeypatch.setattr("alice.speech.synthesis.version", lambda name: "3.1.0")


def test_adapter_is_lazy_cpu_seeded_and_reuses_voice(monkeypatch):
    calls = []

    class Model:
        sample_rate = 24000
        config = SimpleNamespace(model_dump_json=lambda: '{"model":"test"}')

        @classmethod
        def load_model(cls, **kwargs):
            calls.append(("load", kwargs))
            return cls()

        def get_state_for_audio_prompt(self, voice):
            calls.append(("voice", voice))
            return {"voice": voice}

        def generate_audio(self, state, text, **kwargs):
            assert state == {"voice": "alba"}
            assert kwargs["copy_state"] is True
            return torch.rand(240)

    monkeypatch.setitem(sys.modules, "pocket_tts", SimpleNamespace(TTSModel=Model))
    engine = PocketSynthesizer()
    assert not calls
    previous_rng = torch.get_rng_state().clone()
    first = engine.synthesize("Hello", voice="alba", seed=3)
    second = engine.synthesize("Hello", voice="alba", seed=3)
    assert np.array_equal(first.pcm, second.pcm)
    assert torch.equal(previous_rng, torch.get_rng_state())
    assert calls == [("load", {"language": "english_2026-01"}), ("voice", "alba")]
    assert engine.identity["model"] == "english_2026-01"
    with pytest.raises(ValueError):
        engine.synthesize("hello", voice="https://example.com/voice.wav", seed=0)


def test_import_does_not_import_model_or_open_audio_device():
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import alice.speech.synthesis; "
            "assert 'pocket_tts' not in sys.modules; "
            "assert 'sounddevice' not in sys.modules; "
            "assert 'serial' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_background_generation_can_update_cached_tensors(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    class ThreadedModel:
        sample_rate = 24000
        config = SimpleNamespace(model_dump_json=lambda: "{}")

        @classmethod
        def load_model(cls, **kwargs):
            return cls()

        def get_state_for_audio_prompt(self, voice):
            return torch.zeros(240)

        def generate_audio(self, state, text, **kwargs):
            # Pocket TTS's generator thread updates KV tensors in place.
            with ThreadPoolExecutor(1) as worker:
                worker.submit(state.add_, 0.1).result()
            return state

    monkeypatch.setitem(
        sys.modules, "pocket_tts", SimpleNamespace(TTSModel=ThreadedModel)
    )
    clip = PocketSynthesizer().synthesize("Hello", voice="alba", seed=0)
    assert np.allclose(clip.pcm, 0.1)


def test_stream_yields_before_final_and_reuses_copied_voice_state(monkeypatch):
    calls = []

    class Model:
        sample_rate = 24000
        config = SimpleNamespace(model_dump_json=lambda: "{}")

        @classmethod
        def load_model(cls, **kwargs):
            calls.append("load")
            torch.rand(10)
            return cls()

        def get_state_for_audio_prompt(self, voice):
            calls.append(voice)
            return {}

        def generate_audio_stream(self, state, text, *, copy_state):
            assert copy_state
            yield torch.rand(240)
            calls.append("finished")

    monkeypatch.setitem(sys.modules, "pocket_tts", SimpleNamespace(TTSModel=Model))
    engine = PocketSynthesizer()
    stream = engine.stream("Hello", voice="azelma", seed=29)
    first = next(stream)
    assert calls == ["load", "azelma"]
    assert list(stream) == []
    again = list(engine.stream("Hello", voice="azelma", seed=29))
    assert np.array_equal(first.pcm, again[0].pcm)


def test_stream_bounds_pocket_internal_decoder_queues_without_global_patch(monkeypatch):
    import queue

    pocket_module = SimpleNamespace(queue=queue)

    class Model:
        sample_rate = 24000
        config = SimpleNamespace(model_dump_json=lambda: "{}")

        @classmethod
        def load_model(cls, **kwargs):
            return cls()

        def get_state_for_audio_prompt(self, voice):
            return {}

        def generate_audio_stream(self, state, text, *, copy_state):
            channel = pocket_module.queue.Queue()
            channel.put_nowait("first")
            with pytest.raises(queue.Full):
                channel.put_nowait("second")
            assert queue.Queue().maxsize == 0
            yield torch.zeros(240)

    monkeypatch.setitem(sys.modules, "pocket_tts", SimpleNamespace(TTSModel=Model))
    monkeypatch.setitem(sys.modules, "pocket_tts.models.tts_model", pocket_module)
    clip = next(
        PocketSynthesizer(stream_queue_capacity=1).stream(
            "Hello", voice="azelma", seed=29
        )
    )
    assert len(clip.pcm) == 240
