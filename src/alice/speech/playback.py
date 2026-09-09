"""Optional speaker playback; semantic consumers follow the audio DAC clock."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from threading import Event
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from alice.speech.timeline import AudioClip, PreparedSpeech, SpeechFrame


class AudioPlayback:
    """Callback PCM source. No motion work or file I/O runs on the audio thread."""

    def __init__(self, audio: AudioClip) -> None:
        self.audio = audio
        self.error: str | None = None
        self._submitted = 0
        self._dac_start: float | None = None
        self._device: Any = None

    def _callback(
        self,
        outdata: NDArray[np.float32],
        frames: int,
        timing: Any,
        status: Any,
    ) -> None:
        outdata.fill(0)
        if status:
            self.error = f"audio output lost synchronization: {status}"
            raise self._device.CallbackAbort
        if self._dac_start is None:
            self._dac_start = float(timing.outputBufferDacTime)
        count = min(frames, len(self.audio.pcm) - self._submitted)
        outdata[:count, 0] = self.audio.pcm[self._submitted : self._submitted + count]
        self._submitted += count
        if self._submitted == len(self.audio.pcm):
            raise self._device.CallbackStop

    def sample_position(self, device_time: float) -> int | None:
        """None before first audible sample; submitted buffers are not playback."""
        if self._dac_start is None or device_time < self._dac_start:
            return None
        return min(
            self._submitted,
            max(
                0,
                math.floor(
                    (device_time - self._dac_start) * self.audio.sample_rate + 1e-6
                ),
            ),
        )


def play_speech(
    prepared: PreparedSpeech,
    emit: Callable[[SpeechFrame], None],
    *,
    cancel: Event | None = None,
) -> Literal["completed", "cancelled"]:
    """Play locally and emit current speech context; caller owns motion validation.

    Missed frames are coalesced, not replayed in a burst. Underflow or callback
    failure aborts, and all exits release speech ownership. No actuator exists
    in this module. The callback consumer should return promptly.
    """
    if cancel is not None and cancel.is_set():
        emit(
            SpeechFrame(
                sample_index=0,
                mouth_aperture=0,
                speech_weight=0,
                vector=(0, 0, 0),
                intensity=0,
            )
        )
        return "cancelled"
    try:
        import sounddevice as sd  # type: ignore[import-untyped]
    except (ImportError, OSError) as error:
        raise RuntimeError(
            "Speaker playback needs sounddevice and libportaudio2; "
            "the generated preview.html can also play the WAV"
        ) from error
    player = AudioPlayback(prepared.audio)
    player._device = sd
    last_sample = -1
    current_sample = 0
    completed = False
    try:
        with sd.OutputStream(
            samplerate=prepared.audio.sample_rate,
            channels=1,
            dtype="float32",
            callback=player._callback,
        ) as stream:
            try:
                while stream.active:
                    sample = player.sample_position(float(stream.time))
                    if sample is not None:
                        current_sample = sample
                    if cancel is not None and cancel.is_set():
                        stream.abort()
                        return "cancelled"
                    if player.error:
                        raise RuntimeError(player.error)
                    if sample is not None:
                        frame = prepared.at_sample(sample)
                        if frame.sample_index != last_sample:
                            emit(frame)
                            last_sample = frame.sample_index
                    time.sleep(0.005)
                if player.error:
                    raise RuntimeError(player.error)
            except BaseException:
                # Normal context exit drains audio; faults must discard it first.
                stream.abort()
                raise
        completed = True
    finally:
        emit(
            SpeechFrame(
                sample_index=len(prepared.audio.pcm) if completed else current_sample,
                mouth_aperture=0,
                speech_weight=0,
                vector=(0, 0, 0),
                intensity=0,
            )
        )
    return "completed"
