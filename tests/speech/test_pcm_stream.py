"""Chunk boundaries must not change envelope/affect or excuse output underruns."""

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from alice.contracts.speech import SpeechSyncConfig
from alice.contracts.speech_stream import SpeechClause
from alice.speech.pcm_stream import PcmRingBuffer, PcmTimeline, StreamingAudioPlayback
from alice.speech.tts_worker import PcmChunk

RATE = 24000
SYNC = SpeechSyncConfig(full_open_rms=0.06, open_position=1)


def clause(sequence=0, **kwargs):
    return SpeechClause(
        generation_id="g",
        clause_id=f"c{sequence}",
        sequence=sequence,
        text="Hello",
        seed=29,
        vector=(0.7 if sequence == 0 else -0.6, 0, 0),
        intensity=0.7,
        **kwargs,
    )


def test_irregular_chunks_preserve_envelope_windows_and_state():
    pcm = np.concatenate(
        (np.full(1103, 0.06), np.zeros(907), np.full(781, 0.04))
    ).astype(np.float32)
    timelines = []
    for boundaries in ([len(pcm)], [1, 137, 481, 995, 1503, 2612, len(pcm)]):
        timeline = PcmTimeline(RATE, "g", SYNC)
        timeline.commit_clause(clause())
        start = 0
        for i, end in enumerate(boundaries):
            timeline.append(
                PcmChunk("g", "c0", i, RATE, pcm[start:end], final=end == len(pcm))
            )
            start = end
        timeline.finish()
        timelines.append(timeline)
    for sample in range(0, timelines[0].generated_samples + 1, 97):
        assert timelines[0].frame_at(sample) == timelines[1].frame_at(sample)
    assert timelines[0].frame_at(0).mouth_aperture == pytest.approx(0.48658288)
    assert timelines[0].frame_at(timelines[0].generated_samples).speech_weight == 0


def test_future_affect_never_leads_with_mouth_and_tail_closes():
    timeline = PcmTimeline(RATE, "g", SYNC)
    timeline.commit_clause(clause())
    timeline.append(PcmChunk("g", "c0", 0, RATE, np.zeros(4800, np.float32), True))
    timeline.commit_clause(clause(1, end_of_response=True))
    timeline.append(PcmChunk("g", "c1", 0, RATE, np.full(4800, 0.06, np.float32), True))
    timeline.finish()
    before = timeline.led_frame(4000)
    assert before.vector[0] == 0.7
    assert before.mouth_aperture > 0.8
    assert timeline.led_frame(4800).vector[0] == -0.6
    assert timeline.led_frame(timeline.generated_samples).speech_weight == 0
    assert timeline.led_frame(timeline.generated_samples).mouth_aperture == 0


def test_explicit_silence_closes_and_unavailable_lookahead_is_rejected():
    timeline = PcmTimeline(RATE, "g", SYNC)
    timeline.commit_clause(clause())
    timeline.append(PcmChunk("g", "c0", 0, RATE, np.full(4800, 0.06, np.float32), True))
    with pytest.raises(BufferError):
        timeline.led_frame(4000)
    silence = timeline.append_silence(2400)
    assert not np.any(silence)
    assert timeline.frame_at(4800).mouth_aperture == 0
    with pytest.raises(ValueError):
        timeline.append(PcmChunk("old", "c0", 1, RATE, np.zeros(480, np.float32)))


def test_ring_backpressure_and_callback_use_dac_not_submitted_samples():
    async def check():
        ring = PcmRingBuffer(960)
        await ring.put(np.arange(960, dtype=np.float32) / 960)
        task = asyncio.create_task(ring.put(np.ones(480, np.float32)))
        await asyncio.sleep(0)
        assert not task.done()
        player = StreamingAudioPlayback(ring, RATE)
        output = np.empty((480, 1), np.float32)
        assert (
            player.callback(output, 480, SimpleNamespace(outputBufferDacTime=10), None)
            == "continue"
        )
        assert output[100, 0] == pytest.approx(100 / 960)
        assert player.sample_position(9.99) is None
        assert player.sample_position(10.01) == 240
        assert player.submitted_samples == 480
        await task
        assert ring.depth == 960 and ring.max_depth == 960
        ring.abort()
        assert ring.depth == 0

    asyncio.run(check())


def test_underflow_aborts_but_final_partial_callback_is_successful():
    async def check():
        ring = PcmRingBuffer(960)
        await ring.put(np.ones(240, np.float32))
        player = StreamingAudioPlayback(ring, RATE)
        out = np.empty((480, 1), np.float32)
        timing = SimpleNamespace(outputBufferDacTime=10)
        assert player.callback(out, 480, timing, None) == "abort"
        assert player.error and player.underflows == 1
        assert not np.any(out)
        ring = PcmRingBuffer(960)
        await ring.put(np.ones(240, np.float32))
        ring.finish()
        player = StreamingAudioPlayback(ring, RATE)
        assert player.callback(out, 480, timing, None) == "stop"
        assert np.all(out[:240] == 1) and np.all(out[240:] == 0)
        assert not player.error

    asyncio.run(check())
