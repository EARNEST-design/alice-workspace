import numpy as np
import pytest


def endpoint():
    from alice.conversation.vad import Endpoint

    return Endpoint()


def frame(value=0.0):
    return np.full(512, value, np.float32)


def test_silence_never_creates_an_utterance():
    detector = endpoint()
    for _ in range(100):
        update = detector.push(frame(), 0.01)
        assert update.audio is None
        assert not update.speaking


def test_endpoint_keeps_preroll_and_waits_for_320ms_silence():
    detector = endpoint()
    for _ in range(10):
        detector.push(frame(0.1), 0.01)
    for _ in range(3):
        detector.push(frame(0.2), 0.9)
    for _ in range(9):
        assert detector.push(frame(), 0.01).audio is None
    update = detector.push(frame(), 0.01)
    assert update.audio is not None
    assert len(update.audio) == 3200 + 3 * 512 + 10 * 512
    np.testing.assert_array_equal(update.audio[:3200], np.full(3200, 0.1, np.float32))
    assert update.silence_ms == 320
    assert update.speech_probability_mean == pytest.approx(2.8 / 13)
    assert update.voiced_fraction == pytest.approx(3 / 13)
    assert update.voiced_seconds == pytest.approx(1536 / 16000)
    assert update.discard_reason is None


def test_short_click_is_discarded_and_next_speech_can_start():
    detector = endpoint()
    detector.push(frame(0.2), 0.9)
    for _ in range(10):
        assert detector.push(frame(), 0.01).audio is None
    assert detector.push(frame(0.2), 0.9).started


def test_midrange_probability_resets_silence_count():
    detector = endpoint()
    for _ in range(3):
        detector.push(frame(0.2), 0.9)
    for _ in range(8):
        detector.push(frame(), 0.01)
    detector.push(frame(0.1), 0.4)
    for _ in range(9):
        assert detector.push(frame(), 0.01).audio is None
    assert detector.push(frame(), 0.01).audio is not None


def test_overlong_utterance_emits_one_discard_without_audio_or_buffer_growth():
    detector = endpoint()
    updates = [detector.push(frame(0.2), 0.9) for _ in range(500)]

    discarded = [update for update in updates if update.discard_reason]
    assert len(discarded) == 1
    assert discarded[0].discard_reason == "overlong_utterance"
    assert all(update.audio is None for update in updates)
    assert detector.chunks == []
    assert detector.preroll.size == 0
    assert detector.samples == 0

    for _ in range(500):
        update = detector.push(frame(0.2), 0.9)
        assert update.audio is None
        assert update.discard_reason is None
        assert not update.started
    assert detector.chunks == []
    assert detector.preroll.size == 0


def test_overlong_discard_recovers_only_after_320ms_of_quiet():
    detector = endpoint()
    while True:
        update = detector.push(frame(0.2), 0.9)
        if update.discard_reason:
            break

    for _ in range(9):
        update = detector.push(frame(), 0.01)
        assert not update.started
        assert update.audio is None
    assert not detector.push(frame(0.2), 0.9).started

    for _ in range(10):
        update = detector.push(frame(), 0.01)
        assert not update.started
        assert update.audio is None
    recovered = detector.push(frame(0.2), 0.9)
    assert recovered.started
    assert recovered.speaking


def test_short_discard_has_no_speech_statistics():
    detector = endpoint()
    detector.push(frame(0.2), 0.9)
    for _ in range(10):
        update = detector.push(frame(), 0.01)

    assert update.audio is None
    assert update.speech_probability_mean is None
    assert update.voiced_fraction is None
    assert update.voiced_seconds is None


@pytest.mark.parametrize(
    "bad", [np.zeros(511), np.full(512, np.nan), np.full(512, 1.1)]
)
def test_invalid_audio_is_rejected(bad):
    with pytest.raises(ValueError):
        endpoint().push(bad, 0.5)
