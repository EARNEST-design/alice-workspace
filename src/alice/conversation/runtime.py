"""Bounded attended conversation orchestration, without robot actuation."""

from __future__ import annotations

import asyncio
import math
import re
import time
import uuid
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from alice.conversation.audio import capture, inspect_routes
from alice.conversation.vad import Endpoint

_CANCEL_TIMEOUT_SECONDS = 2.0
_OVERLAP_EVIDENCE_KEYS = {
    "overlap_probability_mean",
    "overlap_fraction",
    "overlap_seconds",
    "speech_fraction",
    "speech_seconds",
    "max_simultaneous_speakers",
    "max_window_speakers",
    "model",
}
_WAKE_PATTERN = re.compile(
    r"^\s*(?:(?:hey|hi|hello|喂|你好)\s*[,，:：.!?！？。]?\s*)?"
    r"(?:alice(?=$|[\s,，:：.!?！？。])|愛麗絲|爱丽丝)",
    re.IGNORECASE,
)


def _validated_overlap_evidence(
    value: object, *, audio_seconds: float
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _OVERLAP_EVIDENCE_KEYS:
        raise ValueError("overlap analysis returned an incompatible evidence schema")

    def bounded_number(name: str, maximum: float) -> float:
        item = value[name]
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(item)
            or not 0 <= item <= maximum
        ):
            raise ValueError(f"overlap evidence {name} is invalid")
        return float(item)

    bounded_number("overlap_probability_mean", 1.0)
    overlap_fraction = bounded_number("overlap_fraction", 1.0)
    overlap_seconds = bounded_number("overlap_seconds", audio_seconds)
    speech_fraction = bounded_number("speech_fraction", 1.0)
    speech_seconds = bounded_number("speech_seconds", audio_seconds)
    if overlap_fraction > speech_fraction or overlap_seconds > speech_seconds:
        raise ValueError("overlap evidence exceeds detected speech")

    for name, maximum in (
        ("max_simultaneous_speakers", 2),
        ("max_window_speakers", 3),
    ):
        item = value[name]
        if (
            isinstance(item, bool)
            or not isinstance(item, int)
            or not 0 <= item <= maximum
        ):
            raise ValueError(f"overlap evidence {name} is invalid")

    model = value["model"]
    if not isinstance(model, str) or not model.strip():
        raise ValueError("overlap evidence model identifier is invalid")

    return dict(value)


class BenchRuntime:
    def __init__(
        self,
        store: Any,
        asr: Any,
        models: Any,
        output: Any,
        *,
        vad_factory: Callable[[], Any],
        replay_path: Path | None = None,
        duration_s: float = 300,
        barge_in: bool = False,
        wake_window_s: float = 30,
        overlap_detector: Any | None = None,
    ) -> None:
        if not 1 <= duration_s <= 300:
            raise ValueError("session duration must be 1–300 seconds")
        if not 1 <= wake_window_s <= 300:
            raise ValueError("wake window must be 1–300 seconds")
        self.store, self.asr, self.models, self.output = store, asr, models, output
        self.vad_factory, self.replay_path = vad_factory, replay_path
        self.duration_s, self.barge_in = duration_s, barge_in
        self.wake_window_s = wake_window_s
        self.overlap_detector = overlap_detector
        self._analysis_task: asyncio.Task[Any] | None = None
        self.generation = 0
        self.incarnation = uuid.uuid4().hex
        self.mode = "listen"
        self.history: list[tuple[str, str]] = []
        self.session_task: asyncio.Task[None] | None = None
        self.turn_task: asyncio.Task[None] | None = None
        self._control_intent = 0
        self._lifecycle_lock = asyncio.Lock()
        self._active_stages: dict[str, str] = {}
        self._awake_until: float | None = None
        self.store.emit(
            "session",
            "idle",
            message="Choose Listen or Conversation; Start enables microphone.",
        )

    def identity(self, generation: int) -> str:
        return f"{self.incarnation}-{generation}"

    def _reserve_control_intent(self) -> int:
        self._control_intent += 1
        return self._control_intent

    def _activate(self, stage: str, identity: str) -> None:
        self._active_stages[stage] = identity

    def _complete(self, stage: str, identity: str) -> None:
        if self._active_stages.get(stage) == identity:
            del self._active_stages[stage]

    def _terminal(
        self, stage: str, identity: str, state: str, **details: object
    ) -> None:
        if self._active_stages.get(stage) != identity:
            return
        del self._active_stages[stage]
        self.store.emit(stage, state, generation_id=identity, **details)

    def _retire_active_stages(self) -> None:
        active, self._active_stages = self._active_stages, {}
        for stage, identity in active.items():
            self.store.emit(
                stage,
                "stopped" if stage == "vad" else "cancelled",
                generation_id=identity,
                message="Stopped by lifecycle control",
            )

    def _set_waiting(self) -> None:
        self._awake_until = None
        self.store.emit(
            "engagement",
            "waiting",
            message="Say Alice, 愛麗絲, or 爱丽丝 to begin.",
        )

    def _open_wake_window(self, message: str) -> None:
        self._awake_until = time.monotonic() + self.wake_window_s
        self.store.emit(
            "engagement",
            "awake",
            remaining_seconds=self.wake_window_s,
            message=message,
        )

    def _expire_wake_window(self, now: float | None = None) -> bool:
        if self._awake_until is None:
            return False
        if (time.monotonic() if now is None else now) < self._awake_until:
            return False
        self._awake_until = None
        self.store.emit(
            "engagement",
            "expired",
            remaining_seconds=0.0,
            message="Follow-up window expired; awaiting wake name.",
        )
        return True

    def _cancel_engagement(self, message: str) -> None:
        self._awake_until = None
        self.store.emit(
            "engagement",
            "cancelled",
            remaining_seconds=0.0,
            message=message,
        )

    async def start(self, mode: str = "listen") -> None:
        if mode not in {"listen", "conversation"}:
            raise ValueError("invalid mode")
        if self.session_task and not self.session_task.done():
            raise RuntimeError("session already active")
        intent = self._reserve_control_intent()
        await self._cancel_turn()
        async with self._lifecycle_lock:
            if intent != self._control_intent:
                return
            await self._cancel_session()
            if intent != self._control_intent:
                return
            self.mode = mode
            self.history.clear()
            self._set_waiting()
            self.session_task = asyncio.create_task(self._capture(intent))

    async def _capture(self, intent: int) -> None:
        source_stream = None
        fault: str | None = None
        try:
            self.store.emit("session", "preparing", mode=self.mode)
            source, sink = await inspect_routes()
            self.output.sink = sink
            vad = await asyncio.to_thread(self.vad_factory)
            await self.output.warm()
            endpoint = Endpoint()
            started, last_meter = time.monotonic(), 0.0
            self.store.emit(
                "session",
                "listening",
                mode=self.mode,
                message="Experimental barge-in"
                if self.barge_in
                else "Playback guard enabled",
            )
            source_stream = capture(source)
            async for frame in source_stream:
                if intent != self._control_intent:
                    break
                now = time.monotonic()
                self._expire_wake_window(now)
                if now - started >= self.duration_s:
                    break
                probability = vad.probability(frame)
                replying = self.turn_task is not None and not self.turn_task.done()
                guarded = not self.barge_in and (
                    replying or self.output.busy or now < self.output.guard_until
                )
                if guarded:
                    endpoint.reset()
                    update = None
                    self._complete("vad", self.identity(self.generation))
                else:
                    update = endpoint.push(frame, probability)
                    if update.speaking:
                        self._activate("vad", self.identity(self.generation))
                    else:
                        self._complete("vad", self.identity(self.generation))
                if now - last_meter >= 0.1:
                    self.store.emit(
                        "capture",
                        "active",
                        rms=float(np.sqrt(np.mean(frame**2))),
                        peak=float(np.max(np.abs(frame))),
                        message=source,
                    )
                    self.store.emit(
                        "vad",
                        "reply_guard"
                        if guarded and replying
                        else "playback_guard"
                        if guarded
                        else "speech"
                        if update and update.speaking
                        else "silence",
                        probability=probability,
                        silence_ms=update.silence_ms if update else 0,
                        message="Reply in progress; microphone speech admission paused"
                        if guarded and replying
                        else "",
                        generation_id=self.identity(self.generation),
                    )
                    last_meter = now
                if update and update.discard_reason:
                    self.store.emit(
                        "decision",
                        "advice",
                        text="WAIT",
                        reason=update.discard_reason,
                        message=(
                            "Continuous speech discarded; waiting for 320 ms of quiet."
                        ),
                        generation_id=self.identity(self.generation),
                    )
                if update and update.started and self.barge_in:
                    await self._cancel_turn()
                if update and update.audio is not None:
                    await self._cancel_turn()
                    if intent != self._control_intent:
                        break
                    self.store.emit(
                        "vad",
                        "endpoint",
                        endpoint_ms=update.silence_ms,
                        audio_seconds=len(update.audio) / 16000,
                    )
                    self.turn_task = asyncio.create_task(
                        self.process_utterance(
                            update.audio,
                            audible=True,
                            vad_evidence={
                                "speech_probability_mean": (
                                    update.speech_probability_mean
                                ),
                                "voiced_fraction": update.voiced_fraction,
                                "voiced_seconds": update.voiced_seconds,
                            },
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            fault = str(error)[:300]
        finally:
            if source_stream:
                try:
                    await source_stream.aclose()
                except Exception as error:
                    if fault is None:
                        fault = str(error)[:300]
            if self.session_task is asyncio.current_task():
                self.session_task = None
                if intent == self._control_intent:
                    try:
                        await self._cancel_turn()
                    except Exception as error:
                        if fault is None:
                            fault = str(error)[:300]
                    if intent == self._control_intent:
                        self._cancel_engagement("Session ended; wake window cleared.")
                        self.history.clear()
                        if fault is None:
                            self.store.emit("capture", "stopped")
                            self.store.emit(
                                "session", "idle", message="Microphone stopped"
                            )
                        else:
                            self.store.emit("capture", "error", error=fault)
                            self.store.emit("session", "error", error=fault)

    async def _cancel_turn(self) -> None:
        self.generation += 1
        self._retire_active_stages()
        task, self.turn_task = self.turn_task, None
        try:
            await self.output.stop()
        finally:
            if task and task is not asyncio.current_task() and not task.done():
                task.cancel()
                await self._join_cancelled(task, "turn")

    async def _join_cancelled(self, task: asyncio.Task[Any], label: str) -> None:
        done, _ = await asyncio.wait({task}, timeout=_CANCEL_TIMEOUT_SECONDS)
        if not done:
            raise RuntimeError(f"{label} task did not stop after cancellation")
        await asyncio.gather(task, return_exceptions=True)

    async def _cancel_session(self) -> None:
        task, self.session_task = self.session_task, None
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await self._join_cancelled(task, "session")

    async def stop(self) -> None:
        intent = self._reserve_control_intent()
        self._cancel_engagement("Session stopped; wake window cleared.")
        self.history.clear()
        await self._cancel_turn()
        async with self._lifecycle_lock:
            if intent != self._control_intent:
                return
            await self._cancel_session()
            if intent == self._control_intent:
                self.store.emit("capture", "stopped")
                self.store.emit("session", "idle", message="Microphone stopped")

    async def process_utterance(
        self,
        pcm: NDArray[np.float32],
        *,
        audible: bool = False,
        _replay_wake_bypass: bool = False,
        vad_evidence: dict[str, float | None] | None = None,
    ) -> None:
        generation = self.generation
        identity = self.identity(generation)
        phase, start = "asr", time.monotonic()
        self.store.emit(
            "asr", "uploading", generation_id=identity, audio_seconds=len(pcm) / 16000
        )
        self._activate("asr", identity)
        final: str | None = None
        language: str | None = "English"
        ignored_reason: str | None = None
        partial = ""
        try:
            async for transcript in self.asr.transcribe(pcm):
                if generation != self.generation:
                    return
                if final is not None:
                    raise RuntimeError("ASR returned data after final result")
                if transcript.final:
                    final = transcript.text.strip()
                    language = getattr(transcript, "language", "English")
                    ignored_reason = getattr(transcript, "ignored_reason", None)
                else:
                    partial += transcript.text
                    self.store.emit(
                        "asr",
                        "partial",
                        text=partial,
                        latency_ms=(time.monotonic() - start) * 1000,
                        generation_id=identity,
                    )
            if generation != self.generation:
                return
            if final is None:
                raise RuntimeError("ASR returned no final result")
            self._complete("asr", identity)
            self.store.emit(
                "asr",
                "ignored" if ignored_reason else "final",
                text=final,
                language=language,
                message=ignored_reason or ("No speech detected" if not final else ""),
                latency_ms=(time.monotonic() - start) * 1000,
                generation_id=identity,
            )
            if not final or ignored_reason:
                return
            if language == "Chinese":
                response_language = "Cantonese"
            elif language in {"English", "Cantonese"}:
                response_language = language
            else:
                raise RuntimeError("ASR returned an unsupported reply language")
            phase = "quality"
            evidence = await self._audio_evidence(
                pcm, language, vad_evidence, generation
            )
            if evidence is None or generation != self.generation:
                return
            # This private bypass is reserved for non-audible synthetic replay.
            if _replay_wake_bypass and audible:
                raise ValueError("replay wake bypass cannot enable audible output")
            addressed = _replay_wake_bypass or bool(_WAKE_PATTERN.match(final))
            awake = not self._expire_wake_window() and self._awake_until is not None
            if not addressed and not awake:
                self.store.emit(
                    "decision",
                    "advice",
                    text="WAIT",
                    reason="awaiting_wake",
                    message="Awaiting wake name; no decision or reply request made.",
                    generation_id=identity,
                )
                return
            if _replay_wake_bypass:
                self.store.emit(
                    "decision",
                    "advice",
                    text="SPEAK",
                    reason="replay_wake",
                    message="Synthetic dry replay bypasses wake and decision.",
                    generation_id=identity,
                )
            else:
                phase = "decision"
                advice = await self._advice(
                    final,
                    generation,
                    addressed=addressed,
                    active_conversation=awake,
                    evidence=evidence,
                )
                if advice != "SPEAK" or generation != self.generation:
                    return
                if not addressed and (
                    self._expire_wake_window() or self._awake_until is None
                ):
                    self.store.emit(
                        "decision",
                        "advice",
                        text="WAIT",
                        reason="wake_expired",
                        message="Wake window expired during decision; waiting.",
                        generation_id=identity,
                    )
                    return
            if addressed:
                self._open_wake_window(
                    "Synthetic replay wake bypass."
                    if _replay_wake_bypass
                    else (
                        f"Wake accepted; follow-ups enabled "
                        f"for {self.wake_window_s:g} seconds."
                    )
                )
            phase = "llm"
            await self._reply(
                final,
                generation,
                audible=audible,
                language=response_language,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if generation == self.generation:
                await self.output.stop()
                self._complete(phase, identity)
                self.store.emit(
                    phase, "error", error=str(error)[:300], generation_id=identity
                )

    async def _audio_evidence(
        self,
        pcm: NDArray[np.float32],
        language: str | None,
        vad_evidence: dict[str, float | None] | None,
        generation: int,
    ) -> dict[str, Any] | None:
        identity = self.identity(generation)
        evidence: dict[str, Any] = {
            "asr_confidence": None,
            "language_confidence": None,
            "language_tag": language,
            "audio_seconds": len(pcm) / 16000,
            "rms": float(np.sqrt(np.mean(pcm**2))),
            "clipped_fraction": float(np.mean(np.abs(pcm) >= 0.99)),
            "speech_probability_mean": None,
            "voiced_fraction": None,
            "voiced_seconds": None,
            "overlap": None,
        }
        evidence.update(vad_evidence or {})
        self._activate("quality", identity)
        self.store.emit("quality", "measuring", generation_id=identity)
        start = time.monotonic()
        reason: str | None = None
        if self.overlap_detector is not None:
            try:
                if self._analysis_task is not None and not self._analysis_task.done():
                    raise RuntimeError("Previous audio analysis is still running")
                self._analysis_task = asyncio.create_task(
                    asyncio.to_thread(self.overlap_detector.analyze, pcm)
                )
                # A cancelled turn never queues more native inference. Keep ownership
                # until the existing thread finishes, consuming any late exception.
                self._analysis_task.add_done_callback(
                    lambda task: task.exception() if not task.cancelled() else None
                )
                async with asyncio.timeout(2):
                    result = await asyncio.shield(self._analysis_task)
                overlap = _validated_overlap_evidence(
                    result.as_dict(), audio_seconds=evidence["audio_seconds"]
                )
                evidence["overlap"] = overlap
                if overlap.get("overlap_seconds", 0) >= 0.2:
                    reason = "overlapping_speech"
                elif overlap.get("speech_seconds", 0) < 0.12:
                    reason = "insufficient_speech"
            except asyncio.CancelledError:
                raise
            except Exception:
                reason = "audio_analysis_unavailable"
        if generation != self.generation:
            return None
        self._terminal(
            "quality",
            identity,
            "rejected" if reason else "ready",
            evidence=evidence,
            latency_ms=(time.monotonic() - start) * 1000,
            message={
                "overlapping_speech": "Voices overlap; waiting for one clear voice.",
                "insufficient_speech": "Too little clear speech; ignoring this sound.",
                "audio_analysis_unavailable": "Audio analysis unavailable; waiting.",
            }.get(reason or "", "Audio measured; ASR/language confidence unknown."),
        )
        if reason:
            self.store.emit(
                "decision",
                "advice",
                text="WAIT",
                reason=reason,
                message="Waiting for a clear, single-voice turn.",
                generation_id=identity,
            )
            return None
        return evidence

    async def _advice(
        self,
        text: str,
        generation: int,
        *,
        addressed: bool,
        active_conversation: bool,
        evidence: dict[str, Any],
    ) -> str | None:
        start = time.monotonic()
        identity = self.identity(generation)
        self._activate("decision", identity)
        self.store.emit(
            "decision",
            "thinking",
            message=(
                f"{getattr(self.models, 'decision_label', 'Decision model')} "
                "is checking whether this clear turn addresses Alice."
            ),
            generation_id=identity,
        )
        try:
            result = await self.models.decision(
                text,
                self.history,
                addressed=addressed,
                active_conversation=active_conversation,
                evidence=evidence,
            )
            details = result.as_dict() if hasattr(result, "as_dict") else {}
            result = result.advice if hasattr(result, "advice") else result
            if not isinstance(result, str) or result not in {"SPEAK", "WAIT"}:
                raise RuntimeError("decision model returned invalid advice")
            if generation == self.generation:
                self._terminal(
                    "decision",
                    identity,
                    "advice",
                    text=result,
                    **details,
                    latency_ms=(time.monotonic() - start) * 1000,
                )
                return result
            return None
        except asyncio.CancelledError:
            if generation == self.generation:
                self._terminal("decision", identity, "cancelled")
            raise
        except Exception as error:
            if generation == self.generation:
                self._terminal(
                    "decision",
                    identity,
                    "unavailable",
                    error=(
                        str(error)[:300]
                        or "Decision exceeded its time budget; waiting for a new turn."
                    ),
                )
            return None

    async def _reply(
        self, text: str, generation: int, *, audible: bool, language: str
    ) -> None:
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=4)
        completed: list[str] = []
        identity = self.identity(generation)

        async def produce() -> None:
            start, first = time.monotonic(), True
            pending, full = "", ""
            self._activate("llm", identity)
            self.store.emit("llm", "requesting", generation_id=identity)
            try:
                async for delta in self.models.reply(
                    text, self.history, language=language
                ):
                    if generation != self.generation:
                        return
                    full += delta
                    pending += delta
                    self.store.emit(
                        "llm",
                        "streaming",
                        text=full,
                        generation_id=identity,
                        latency_ms=(time.monotonic() - start) * 1000 if first else None,
                    )
                    first = False
                    while match := re.match(
                        r".*?(?:[.!?](?:\s|$)|[。！？])", pending, flags=re.DOTALL
                    ):
                        clause, pending = (
                            pending[: match.end()].strip(),
                            pending[match.end() :],
                        )
                        await queue.put(clause)
                    if len(pending) > 200:
                        raise RuntimeError("unpunctuated reply exceeds clause limit")
                if pending.strip():
                    await queue.put(pending.strip())
                await queue.put(None)
                if generation == self.generation:
                    self._complete("llm", identity)
                    self.store.emit(
                        "llm",
                        "complete",
                        text=full,
                        latency_ms=(time.monotonic() - start) * 1000,
                        generation_id=identity,
                    )
            finally:
                self._complete("llm", identity)

        async def consume() -> None:
            while (clause := await queue.get()) is not None:
                if generation != self.generation:
                    return
                self._activate("tts", identity)
                try:
                    await self.output.speak(
                        clause, identity, audible=audible, language=language
                    )
                except asyncio.CancelledError:
                    if generation == self.generation:
                        self._terminal("tts", identity, "cancelled")
                    raise
                except Exception as error:
                    if generation == self.generation:
                        self._terminal("tts", identity, "error", error=str(error)[:300])
                    raise
                else:
                    if generation == self.generation:
                        self._terminal("tts", identity, "complete")
                        completed.append(clause)

        succeeded = False
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(produce())
                group.create_task(consume())
            succeeded = True
        finally:
            if succeeded and completed and generation == self.generation:
                separator = "" if language == "Cantonese" else " "
                self.history.append((text, separator.join(completed)))
                self.history = self.history[-4:]
                self._open_wake_window(
                    "Follow-up window refreshed after Alice replied."
                )

    async def replay(self) -> None:
        if self.replay_path is None:
            raise RuntimeError("no synthetic replay fixture configured")
        if self.session_task and not self.session_task.done():
            raise RuntimeError("session already active")
        intent = self._reserve_control_intent()
        await self._cancel_turn()
        async with self._lifecycle_lock:
            if intent != self._control_intent:
                return
            await self._cancel_session()
            if intent != self._control_intent:
                return
            self.mode = "conversation"
            self.history.clear()
            self._set_waiting()
            self.session_task = asyncio.create_task(self._replay(intent))

    async def _replay(self, intent: int | None = None) -> None:
        try:
            assert self.replay_path is not None
            with wave.open(str(self.replay_path), "rb") as source:
                if (
                    source.getframerate() != 16000
                    or source.getsampwidth() != 2
                    or source.getnchannels() not in {1, 2}
                    or not 0 < source.getnframes() <= 15 * 16000
                ):
                    raise ValueError("replay needs bounded 16k PCM16 mono/stereo WAV")
                pcm = np.frombuffer(
                    source.readframes(source.getnframes()), "<i2"
                ).reshape(-1, source.getnchannels())[:, 0].astype(
                    np.float32
                ) * np.float32(1 / 32768)
            self.store.emit("session", "replay", message="Synthetic input; speaker dry")
            vad = await asyncio.to_thread(self.vad_factory)
            await self.output.warm()
            endpoint = Endpoint()
            padded = np.pad(pcm, (0, 512 - len(pcm) % 512 + 5120))
            for offset in range(0, len(padded), 512):
                if intent is not None and intent != self._control_intent:
                    break
                frame = padded[offset : offset + 512]
                probability = vad.probability(frame)
                update = endpoint.push(frame, probability)
                vad_identity = self.identity(self.generation)
                if update.speaking:
                    self._activate("vad", vad_identity)
                else:
                    self._complete("vad", vad_identity)
                if offset % (512 * 4) == 0:
                    self.store.emit(
                        "capture",
                        "replay",
                        rms=float(np.sqrt(np.mean(frame**2))),
                        peak=float(np.max(np.abs(frame))),
                        message="Synthetic WAV; microphone inactive",
                    )
                    self.store.emit(
                        "vad",
                        "speech" if update.speaking else "silence",
                        probability=probability,
                        silence_ms=update.silence_ms,
                        generation_id=vad_identity,
                    )
                    await asyncio.sleep(0)
                if update.audio is not None:
                    self.generation += 1
                    self.store.emit("vad", "endpoint", endpoint_ms=update.silence_ms)
                    await self.process_utterance(
                        update.audio,
                        audible=False,
                        _replay_wake_bypass=True,
                        vad_evidence={
                            "speech_probability_mean": (update.speech_probability_mean),
                            "voiced_fraction": update.voiced_fraction,
                            "voiced_seconds": update.voiced_seconds,
                        },
                    )
            self.store.emit("capture", "stopped", message="Synthetic input finished")
            self.store.emit(
                "session",
                "replay_complete",
                message="Synthetic replay finished; no sound played",
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.store.emit("session", "error", error=str(error)[:300])
        finally:
            if intent is not None and self.session_task is asyncio.current_task():
                self.session_task = None
                await self._cancel_turn()
                if intent == self._control_intent:
                    self._cancel_engagement(
                        "Synthetic replay ended; wake window cleared."
                    )
                    self.history.clear()

    async def close(self) -> None:
        await self.stop()
        await self.asr.close()
        await self.models.close()
        await self.output.close()
