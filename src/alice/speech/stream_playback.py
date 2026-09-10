"""Playback drivers. The simulated DAC is the device-free default."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import numpy as np

from alice.speech.pcm_stream import PcmRingBuffer, PcmTimeline, StreamingAudioPlayback
from alice.speech.timeline import SpeechFrame


class SimulatedPlayback:
    async def __call__(
        self,
        timeline: PcmTimeline,
        ring: PcmRingBuffer,
        emit: Callable[[SpeechFrame], Awaitable[None]],
        cancel: asyncio.Event,
        metrics: dict[str, object],
    ) -> None:
        metrics["clock_kind"] = "simulated-dac"
        player = StreamingAudioPlayback(ring, timeline.sample_rate)
        count = round(timeline.sample_rate * 0.02)
        out = np.empty((count, 1), np.float32)
        started = time.monotonic()
        sample = 0
        while not cancel.is_set():
            result = player.callback(
                out,
                count,
                SimpleNamespace(outputBufferDacTime=sample / timeline.sample_rate),
                None,
            )
            metrics["underflows"] = player.underflows
            if player.error:
                raise RuntimeError(player.error)
            if sample < timeline.generated_samples:
                await emit(timeline.led_frame(sample))
            sample = player.submitted_samples
            await asyncio.sleep(
                max(0, started + sample / timeline.sample_rate - time.monotonic())
            )
            metrics["played_samples"] = sample
            if result == "stop":
                return


class SoundDevicePlayback:
    async def __call__(
        self,
        timeline: PcmTimeline,
        ring: PcmRingBuffer,
        emit: Callable[[SpeechFrame], Awaitable[None]],
        cancel: asyncio.Event,
        metrics: dict[str, object],
    ) -> None:
        import sounddevice as sd  # type: ignore[import-untyped]

        metrics["clock_kind"] = "portaudio-dac"
        player = StreamingAudioPlayback(ring, timeline.sample_rate)

        def callback(out: Any, frames: int, timing: Any, status: Any) -> None:
            result = player.callback(out, frames, timing, status)
            if result == "abort":
                raise sd.CallbackAbort
            if result == "stop":
                raise sd.CallbackStop

        stream = await asyncio.to_thread(
            sd.OutputStream,
            samplerate=timeline.sample_rate,
            channels=1,
            dtype="float32",
            callback=callback,
            blocksize=round(timeline.sample_rate * 0.02),
        )
        try:
            await asyncio.to_thread(stream.start)
            last_sample = -1
            while stream.active:
                if cancel.is_set():
                    return
                if player.error:
                    raise RuntimeError(player.error)
                sample = player.sample_position(float(stream.time))
                if sample is not None and sample < timeline.generated_samples:
                    metrics["played_samples"] = sample
                    if sample - last_sample >= round(timeline.sample_rate * 0.02):
                        await emit(timeline.led_frame(sample))
                        last_sample = sample
                await asyncio.sleep(0.002)
            if player.error:
                raise RuntimeError(player.error)
            if not ring.finished or ring.depth:
                raise RuntimeError("audio device stopped before the stream finished")
            metrics["played_samples"] = player.submitted_samples
        finally:
            metrics["underflows"] = player.underflows
            await asyncio.to_thread(stream.abort)
            await asyncio.to_thread(stream.close)
