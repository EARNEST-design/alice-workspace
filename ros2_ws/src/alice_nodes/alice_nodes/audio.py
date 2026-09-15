"""Bounded PCM ring and DAC clock; the PortAudio callback performs no DDS/I/O."""

import asyncio
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
from alice_interfaces.msg import PcmChunk, PcmCredit, PlaybackStatus, SpeechState

from alice.contracts.speech import SpeechSyncConfig
from alice.speech.pcm_stream import PcmRingBuffer, PcmTimeline, StreamingAudioPlayback
from alice.speech.tts_worker import PcmChunk as DomainChunk
from alice_nodes import contracts as wire
from alice_nodes.base import LATEST, RELIABLE, RuntimeNode, spin, write_json
from alice_nodes.transport import PcmStreamGuard


def validate_audio_budget(
    transport_samples: int, *, tail_samples: int, sample_rate: int
) -> None:
    """Reserve the local tail before admitting any over-budget transport PCM."""
    if transport_samples < 0 or tail_samples < 0 or not 8000 <= sample_rate <= 192000:
        raise ValueError("invalid audio duration budget")
    if transport_samples + tail_samples > 10 * sample_rate:
        raise ValueError("PCM exceeds ten-second total audio budget including tail")


@dataclass(frozen=True)
class AudioCounts:
    transport: int = 0
    generated: int = 0
    submitted: int = 0
    played: int = 0

    def __post_init__(self):
        if (
            not 0 <= self.played <= self.submitted <= self.generated
            or not 0 <= self.transport <= self.generated
        ):
            raise ValueError("invalid audio count ordering")

    @property
    def consumed_transport(self):
        return min(self.transport, self.submitted)


class AudioNode(RuntimeNode):
    def __init__(self, **kwargs):
        super().__init__("audio", **kwargs)
        self.declare_parameter("audio_device_enabled", False)
        self.ring = None
        self.player = None
        self.play_thread = None
        self.status_pub = self.create_publisher(
            PlaybackStatus, "/alice/audio/playback_status", RELIABLE
        )
        self.speech_pub = self.create_publisher(
            SpeechState, "/alice/speech/state", LATEST
        )
        self.credit_pub = self.create_publisher(
            PcmCredit, "/alice/speech/pcm_credit", RELIABLE
        )
        self.subscribe_work(PcmChunk, "/alice/speech/pcm", self.pcm)

    def prepare_run(self):
        sync = SpeechSyncConfig.model_validate_json(
            (self.config_root / "speech/sync-hardware-v1.json").read_text()
        )
        self.timeline = PcmTimeline(24000, self.identity.generation_id, sync)
        self.ring = PcmRingBuffer(48000)
        self.player = StreamingAudioPlayback(self.ring, 24000)
        self.pcm_guard = PcmStreamGuard(self.identity, max_gap_ns=None)
        self.transport_samples = self.played = 0
        self.clause_sequence = 0
        self.response_final = self.drained = self.playing = False
        self.last_dac_ns = None
        self.last_status = None
        self.last_credit = -1
        self.status_sent = 0
        self._timeline_lock = threading.Lock()
        self.raw = None
        if self.get_parameter("retain_raw").value:
            import wave

            self.raw = wave.open(str(self.local_dir / "audio.wav"), "wb")
            self.raw.setnchannels(1)
            self.raw.setsampwidth(2)
            self.raw.setframerate(24000)

    def start_run(self):
        self.play_thread = threading.Thread(
            target=self._play, name="audio-dac-owner", daemon=True
        )
        self.play_thread.start()

    @property
    def counts(self):
        return AudioCounts(
            self.transport_samples,
            self.timeline.generated_samples,
            self.player.submitted_samples,
            self.played,
        )

    def pcm(self, message):
        header = wire.stream_header_from_msg(message.header)
        if not self.admit_header(header, "tts", "pcm", exact=True, sparse=True):
            return
        packet = wire.pcm_packet_from_msg(message)
        validate_audio_budget(
            packet.global_sample_offset + len(packet.samples),
            tail_samples=round(self.timeline.config.tail_s * packet.sample_rate),
            sample_rate=packet.sample_rate,
        )
        self.pcm_guard.admit(packet, now_monotonic_ns=time.monotonic_ns())
        pcm = np.asarray(packet.samples, dtype=np.float32)
        with self._timeline_lock:
            if packet.first_packet:
                self.timeline.commit_clause(packet.clause)
                self.clause_sequence = 0
            self.timeline.append(
                DomainChunk(
                    self.identity.generation_id,
                    packet.clause_id,
                    self.clause_sequence,
                    packet.sample_rate,
                    pcm,
                    packet.clause_final,
                )
            )
            self.clause_sequence += 1
            self.transport_samples += len(pcm)
        if self.raw:
            self.raw.writeframes((pcm * 32767).astype("<i2").tobytes())
        asyncio.run(self.ring.put(pcm))
        if packet.response_final:
            with self._timeline_lock:
                tail = self.timeline.finish()
            if self.raw:
                self.raw.writeframes((tail * 32767).astype("<i2").tobytes())
            asyncio.run(self.ring.put(tail))
            self.response_final = True
            self.ring.finish()
        self.progress = self.timeline.generated_samples

    def _play(self):
        try:
            deadline = time.monotonic() + 5
            while self.ring.depth < 4800 and not self.ring.finished:
                if self.cancel.wait(0.002):
                    return
                if time.monotonic() > deadline:
                    raise RuntimeError("audio startup prebuffer deadline expired")
                self.publish_status()
            if self.cancel.is_set():
                return
            self.playing = True
            if (
                self.binding.hardware
                or self.get_parameter("audio_device_enabled").value
            ):
                self._device_play()
            else:
                self._simulated_play()
            if not self.cancel.is_set():
                if (
                    not self.response_final
                    or not self.ring.finished
                    or self.ring.depth
                    or self.player.error
                ):
                    raise RuntimeError(
                        "audio stopped without complete reliable PCM drain"
                    )
                self.played = self.player.submitted_samples
                if self.played != self.timeline.generated_samples:
                    raise RuntimeError("audio drain counts differ from generation")
                self.drained = True
                self.playing = False
                self.publish_status()
        except Exception as exc:
            self.fail(str(exc))
            self.publish_status()

    def _simulated_play(self):
        count = 480
        out = np.empty((count, 1), np.float32)
        origin = time.monotonic()
        while not self.cancel.is_set():
            result = self.player.callback(
                out,
                count,
                SimpleNamespace(
                    outputBufferDacTime=self.player.submitted_samples / 24000
                ),
                None,
            )
            if result == "abort":
                raise RuntimeError(self.player.error)
            # Simulated device time advances independently of generated/submitted PCM.
            self._emit_clock(time.monotonic() - origin)
            target = origin + self.player.submitted_samples / 24000
            while time.monotonic() < target:
                if self.cancel.wait(0.002):
                    return
                self._emit_clock(time.monotonic() - origin)
            if result == "stop":
                return

    def _device_play(self):
        import sounddevice as sd

        def callback(out, frames, timing, status):
            result = self.player.callback(out, frames, timing, status)
            if result == "abort":
                # Only signal here; the local watchdog publishes faults.
                self.cancel.set()
                raise sd.CallbackAbort
            if result == "stop":
                raise sd.CallbackStop

        stream = sd.OutputStream(
            samplerate=24000,
            channels=1,
            dtype="float32",
            blocksize=480,
            callback=callback,
        )
        try:
            stream.start()
            while stream.active and not self.cancel.is_set():
                self._emit_clock(float(stream.time))
                time.sleep(0.002)
            if self.player.error:
                raise RuntimeError(self.player.error)
            # CallbackStop drains PortAudio's queued output before active becomes false.
        finally:
            stream.abort()
            stream.close()

    def _emit_clock(self, device_time):
        sample = self.player.sample_position(device_time)
        if sample is None:
            return
        if self.last_dac_ns is None or sample > self.played:
            self.last_dac_ns = time.monotonic_ns()
        self.played = sample
        if sample < self.timeline.generated_samples and (
            self.last_status is None or sample - self.last_status >= 480
        ):
            with self._timeline_lock:
                frame = self.timeline.led_frame(sample)
            self.speech_pub.publish(
                wire.speech_state_to_msg(
                    frame,
                    self.header("speech", self.last_dac_ns),
                    sample_rate=24000,
                    phase="playing",
                    owner=self.incarnation,
                )
            )
            self.last_status = sample
            self.progress = self.timeline.generated_samples
            self.publish_status()
        consumed = self.counts.consumed_transport
        if consumed != getattr(self, "last_credit", -1):
            self.credit_pub.publish(
                PcmCredit(
                    header=wire.stream_header_to_msg(self.header("credit")),
                    schema_version="pcm-credit/v1",
                    capacity_samples=48000,
                    cumulative_consumed_samples=consumed,
                )
            )
            self.last_credit = consumed

    def publish_status(self):
        now = time.monotonic_ns()
        if (
            not self.error
            and not self.drained
            and now - getattr(self, "status_sent", 0) < 50_000_000
        ):
            return
        self.status_sent = now
        state = (
            PlaybackStatus.FAULT
            if self.error
            else PlaybackStatus.DRAINED
            if self.drained
            else PlaybackStatus.PLAYING
            if self.playing
            else PlaybackStatus.BUFFERING
        )
        self.status_pub.publish(
            PlaybackStatus(
                header=wire.stream_header_to_msg(self.header("playback")),
                schema_version="playback-status/v1",
                state=state,
                submitted_samples=self.player.submitted_samples,
                played_samples=self.played,
                response_final_seen=self.response_final,
                drained=self.drained,
                error=self.error or "",
            )
        )

    def check_progress(self, now):
        if self.player and self.player.error:
            raise RuntimeError(self.player.error)
        if (
            self.playing
            and self.last_dac_ns is not None
            and now - self.last_dac_ns > 250_000_000
        ):
            raise RuntimeError("DAC progress expired")

    def validate_end(self, outcome):
        if outcome == "success" and not self.drained:
            raise RuntimeError("audio successful drain required")

    def stop_local(self):
        if self.ring:
            self.ring.abort()

    def finalize_run(self, outcome):
        if self.play_thread:
            self.play_thread.join(timeout=2)
            if self.play_thread.is_alive():
                raise RuntimeError("audio cleanup deadline expired")
        if self.raw:
            self.raw.close()
            self.raw = None
        write_json(
            self.local_dir / "audio.json",
            dict(
                vars(self.counts),
                drained=self.drained,
                response_final=self.response_final,
                underflows=self.player.underflows,
                ring_max_depth=self.ring.max_depth,
                clock_kind="portaudio-dac"
                if self.binding.hardware
                or self.get_parameter("audio_device_enabled").value
                else "simulated-dac",
            ),
        )


def create_node(**kwargs):
    return AudioNode(**kwargs)


def main():
    spin(create_node)
