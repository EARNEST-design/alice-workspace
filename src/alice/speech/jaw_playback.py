"""A DAC-clock producer and separate, serial jaw-command consumer."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Event, Lock, Thread
from typing import Protocol

from alice.contracts.motion import TargetUpdate, TargetUpdateHorizon
from alice.contracts.speech import SpeechSyncConfig
from alice.hardware.manifest import HardwareManifest
from alice.speech.composer import compose_frame, expression_at_sample
from alice.speech.jaw_trial import JawPlaybackStream
from alice.speech.playback import play_speech
from alice.speech.timeline import PreparedSpeech, SpeechFrame


class SpeechPlayer(Protocol):
    def __call__(
        self,
        prepared: PreparedSpeech,
        emit: Callable[[SpeechFrame], None],
        *,
        cancel: Event,
    ) -> str: ...


def select_motion_targets(
    proposal: TargetUpdate, manifest: HardwareManifest, active: tuple[str, ...]
) -> TargetUpdate:
    """Wire every semantic channel to the shared manifest, then select this run."""
    for target in proposal.targets:
        manifest.actuator(target.actuator_name).target_qus(target.normalized_position)
    positions = {t.actuator_name: t for t in proposal.targets}
    if not active or len(set(active)) != len(active):
        raise ValueError("active actuator selection must be nonempty and unique")
    for name in active:
        manifest.actuator(name)
        if name not in positions:
            raise ValueError(f"proposal is missing active actuator {name}")
    return TargetUpdate(
        offset_s=proposal.offset_s, targets=tuple(positions[n] for n in active)
    )


def run_jaw_playback(
    prepared: PreparedSpeech,
    stream: JawPlaybackStream,
    *,
    expression: TargetUpdateHorizon | None = None,
    routing_manifest: HardwareManifest | None = None,
    cancel: Event | None = None,
    player: SpeechPlayer = play_speech,
    sleeper: Callable[[float], None] = time.sleep,
    telemetry: dict[str, object] | None = None,
) -> dict[str, object]:
    """Trusted composition helper; tests use fake clocks/audio and mock adapters.

    Cancellation or any error ends authority without automatic recovery motion.
    The interactive root owns approval, real device creation and power-off evidence.
    """
    cancel = cancel or Event()
    telemetry = telemetry if telemetry is not None else {}
    config = stream.config
    routing_manifest = routing_manifest or stream.manifest
    if len(prepared.audio.pcm) / prepared.audio.sample_rate > config.max_audio_s:
        raise ValueError("speech exceeds the short hardware trial duration")
    sync = SpeechSyncConfig(
        closed_position=config.closed_position, open_position=config.open_position
    )
    started = stream.clock()
    lock = Lock()
    latest: tuple[SpeechFrame, int, int] | None = None
    outcome: str | None = None
    error: str | None = None
    frames: list[dict[str, object]] = []
    proposals: list[dict[str, object]] = []
    worker: Thread | None = None
    watchdog_stop = Event()
    watchdog_tripped = Event()
    cleanup_errors: list[str] = []

    def close_adapter() -> None:
        try:
            stream.close()
        except Exception as exc:
            cleanup_errors.append(f"adapter close: {type(exc).__name__}: {exc}")

    def watchdog() -> None:
        while not watchdog_stop.wait(0.05):
            if (stream.clock() - stream.last_progress_ns) / 1e9 > 0.75:
                watchdog_tripped.set()
                cancel.set()
                stream.revoke("jaw watchdog expired")
                close_adapter()
                return

    guardian = Thread(target=watchdog, name="jaw-watchdog", daemon=True)

    def check() -> None:
        if watchdog_tripped.is_set():
            raise RuntimeError("jaw watchdog expired; remove servo power")
        if cancel.is_set():
            raise RuntimeError("jaw trial cancelled; remove servo power")
        if (stream.clock() - started) / 1e9 > config.max_run_s:
            raise RuntimeError("jaw trial exceeded its maximum duration")

    def ramp(target: float) -> None:
        phase_start = stream.clock()
        while True:
            check()
            if (stream.clock() - phase_start) / 1e9 > config.phase_timeout_s:
                raise RuntimeError("jaw entry/exit phase timed out")
            status = stream.step(target, audio_sample=None)
            if (
                status is not None
                and abs(stream.position - target) < 1e-8
                and abs(stream.velocity) < 1e-6
            ):
                # Exact Home is required for supervisor completion.
                if target != 0 or stream.position == 0:
                    return
            sleeper(0.002)

    def emit(frame: SpeechFrame) -> None:
        nonlocal latest
        with lock:
            at = stream.clock()
            frames.append(
                {"frame": frame.model_dump(mode="json"), "observed_monotonic_ns": at}
            )
            latest = frame, at, len(frames)

    def produce() -> None:
        nonlocal outcome, error
        try:
            result = player(prepared, emit, cancel=cancel)
            with lock:
                outcome = result
        except BaseException as exc:
            with lock:
                error = f"{type(exc).__name__}: {exc}"
                outcome = "failed"

    consumed: set[int] = set()
    successful = False
    try:
        check()
        guardian.start()
        ramp(config.closed_position)
        audio_start = stream.clock()
        worker = Thread(target=produce, name="jaw-audio-producer", daemon=True)
        worker.start()
        while True:
            check()
            with lock:
                current, completed, failure = latest, outcome, error
            if completed is not None:
                if completed != "completed":
                    raise RuntimeError(failure or f"audio {completed}")
                break
            if current is None:
                if (stream.clock() - audio_start) / 1e9 > 2:
                    raise RuntimeError("audio did not start before the deadline")
                stream.step(config.closed_position, audio_sample=None)
            else:
                frame, received_ns, revision = current
                if (stream.clock() - received_ns) / 1e9 > config.source_timeout_s:
                    raise RuntimeError("audio sample source stalled")
                # A release frame alone is never evidence of successful playback.
                if frame.speech_weight > 0:
                    base = expression_at_sample(
                        expression,
                        sample_index=frame.sample_index,
                        sample_rate=prepared.audio.sample_rate,
                    )
                    # Only aperture looks ahead into already available PCM.
                    # Affect, ownership and telemetry keep the current DAC sample.
                    mouth_sample = frame.sample_index + round(
                        config.mouth_lead_s * prepared.audio.sample_rate
                    )
                    mouth_frame = (
                        frame.model_copy(
                            update={
                                "mouth_aperture": prepared.at_sample(
                                    mouth_sample
                                ).mouth_aperture
                            }
                        )
                        if config.mouth_lead_s
                        else frame
                    )
                    composed = compose_frame(base, mouth_frame, sync)
                    selected = select_motion_targets(
                        composed, routing_manifest, ("mouth_open",)
                    )
                    status = stream.step(
                        selected.targets[0].normalized_position,
                        audio_sample=frame.sample_index,
                    )
                    if status is not None:
                        consumed.add(revision)
                        proposals.append(composed.model_dump(mode="json"))
            sleeper(0.002)
        ramp(0.0)
        stream.finish()
        successful = True
        return telemetry
    finally:
        watchdog_stop.set()
        cancel.set()
        if not successful:
            stream.revoke("speech trial stopped; remove servo power")
        close_adapter()
        if worker is not None:
            worker.join(timeout=2)
            if worker.is_alive():
                cleanup_errors.append("audio worker did not stop")
        if guardian.is_alive():
            guardian.join(timeout=1)
            if guardian.is_alive():
                cleanup_errors.append("watchdog did not stop")
        with lock:
            telemetry.update(
                {
                    "audio_outcome": outcome,
                    "mouth_lead_s": config.mouth_lead_s,
                    "audio_error": error,
                    "started_monotonic_ns": started,
                    "ended_monotonic_ns": stream.clock(),
                    "audio_frames": list(frames),
                    "composed_proposals": proposals,
                    "consumed_source_frames": len(consumed),
                    "coalesced_source_frames": len(frames) - len(consumed),
                    "controller_home_confirmed": successful,
                    "physical_sync_measured": False,
                    "cleanup_errors": cleanup_errors,
                }
            )
        if successful and cleanup_errors:
            raise RuntimeError("jaw cleanup incomplete: " + "; ".join(cleanup_errors))
