import io
import json

import numpy as np
import pytest


def test_female_voice_worker_uses_cantonese_preset_and_emits_bounded_normalized_pcm():
    from alice.conversation.cosy_worker import synthesize_request

    class Tensor:
        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return np.full((1, 2205), 0.025, np.float32)

    class Engine:
        sample_rate = 22050

        def inference_sft(self, text, voice, **kwargs):
            assert text == "你好呀。"
            assert voice == "粤语女"
            assert kwargs == {"stream": False, "text_frontend": False, "speed": 0.85}
            yield {"tts_speech": Tensor()}

    output = io.BytesIO()
    synthesize_request(Engine(), {"text": "你好呀。", "seed": 7}, output)
    output.seek(0)
    assert json.loads(output.readline()) == {"samples": 2400, "sample_rate": 24000}
    pcm = np.frombuffer(output.read(2400 * 4), "<f4")
    assert np.sqrt(np.mean(pcm * pcm)) == pytest.approx(0.12, abs=0.001)
    assert json.loads(output.readline()) == {"done": True}
    assert not output.read()


@pytest.mark.parametrize(
    "pcm,rate",
    [
        (np.zeros(3), 16000),
        (np.zeros((2, 10)), 22050),
        (np.array([np.nan]), 22050),
        (np.zeros(22050 * 16), 22050),
        (np.array([]), 22050),
    ],
)
def test_female_audio_rejects_bad_shape_rate_duration_and_nonfinite(pcm, rate):
    from alice.conversation.cosy_worker import prepare_audio

    with pytest.raises(ValueError):
        prepare_audio(pcm, rate)


@pytest.mark.parametrize(
    "payload",
    [
        {"text": ""},
        {"text": "x" * 201, "seed": 7},
        {"text": "你好。", "seed": True},
        {"text": "你好。", "seed": -1},
    ],
)
def test_invalid_requests_fail_before_model_invocation(payload):
    from alice.conversation.cosy_worker import synthesize_request

    with pytest.raises(ValueError):
        synthesize_request(None, payload, io.BytesIO())


def test_cantonese_text_keeps_spoken_words_while_normalizing_script_for_voice():
    from alice.conversation.cosy_worker import voice_text

    assert voice_text("你好呀，我係愛麗絲。") == "你好呀，我系爱丽丝。"
    assert voice_text("你想傾啲咩呀？") == "你想倾啲咩呀？"
