import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from alice.conversation.events import EventStore
from alice.conversation.runtime import BenchRuntime


class Asr:
    async def transcribe(self, pcm):
        yield SimpleNamespace(
            text="Alice, purple yesterday spoon.", final=True, language="English"
        )


class Output:
    def __init__(self):
        self.spoken = []

    async def stop(self):
        pass

    async def speak(self, text, generation, *, audible, language):
        self.spoken.append(text)


class Models:
    def __init__(self, advice="WAIT"):
        self.advice = advice
        self.contexts = []

    async def decision(self, text, history, **context):
        self.contexts.append(context)
        return self.advice

    async def reply(self, text, history, *, language):
        yield "Hello."


def overlap_evidence(**changes):
    values = {
        "overlap_probability_mean": 0.0,
        "overlap_fraction": 0.0,
        "overlap_seconds": 0.0,
        "speech_fraction": 0.8,
        "speech_seconds": 0.8,
        "max_simultaneous_speakers": 1,
        "max_window_speakers": 1,
        "model": "synthetic-pinned-overlap-model",
    }
    values.update(changes)
    return values


def test_possible_wake_must_pass_decision_before_opening_window():
    async def run():
        store, output, models = EventStore(), Output(), Models()
        service = BenchRuntime(store, Asr(), models, output, vad_factory=lambda: None)
        await service.process_utterance(np.full(16000, 0.1, np.float32))
        assert output.spoken == []
        assert service._awake_until is None
        assert len(models.contexts) == 1
        context = models.contexts[0]
        assert context["addressed"] is True
        assert context["active_conversation"] is False
        assert context["evidence"]["asr_confidence"] is None
        assert context["evidence"]["language_confidence"] is None
        assert context["evidence"]["overlap"] is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "speaker_case,reason",
    [("overlap", "overlapping_speech"), ("no_speech", "insufficient_speech")],
)
def test_audio_rejection_blocks_wake_and_downstream_models(speaker_case, reason):
    async def run():
        class Analysis:
            def analyze(self, pcm):
                values = overlap_evidence(
                    overlap_probability_mean=(
                        0.4 if speaker_case == "overlap" else 0.0
                    ),
                    overlap_fraction=0.4 if speaker_case == "overlap" else 0.0,
                    overlap_seconds=0.4 if speaker_case == "overlap" else 0.0,
                    speech_fraction=0.8 if speaker_case == "overlap" else 0.01,
                    speech_seconds=0.8 if speaker_case == "overlap" else 0.01,
                    max_simultaneous_speakers=(2 if speaker_case == "overlap" else 1),
                    max_window_speakers=2 if speaker_case == "overlap" else 0,
                )
                return SimpleNamespace(as_dict=lambda: values)

        store, output, models = EventStore(), Output(), Models("SPEAK")
        service = BenchRuntime(
            store,
            Asr(),
            models,
            output,
            vad_factory=lambda: None,
            overlap_detector=Analysis(),
        )
        await service.process_utterance(np.full(16000, 0.1, np.float32))
        assert output.spoken == [] and models.contexts == []
        assert service._awake_until is None
        assert store.snapshot()["stages"]["decision"]["details"]["reason"] == reason

    asyncio.run(run())


def test_single_voice_evidence_reaches_decision_and_admitted_wake():
    async def run():
        values = overlap_evidence()

        class Analysis:
            def analyze(self, pcm):
                return SimpleNamespace(as_dict=lambda: values)

        store, output, models = EventStore(), Output(), Models("SPEAK")
        service = BenchRuntime(
            store,
            Asr(),
            models,
            output,
            vad_factory=lambda: None,
            overlap_detector=Analysis(),
        )
        await service.process_utterance(
            np.full(16000, 0.1, np.float32),
            vad_evidence={
                "voiced_seconds": 0.5,
                "speech_probability_mean": 0.7,
                "voiced_fraction": 0.6,
            },
        )
        assert output.spoken == ["Hello."]
        assert service._awake_until is not None
        evidence = models.contexts[0]["evidence"]
        assert evidence["overlap"] == values
        assert evidence["voiced_seconds"] == 0.5
        assert evidence["speech_probability_mean"] == 0.7
        assert evidence["asr_confidence"] is None

    asyncio.run(run())


@pytest.mark.parametrize(
    ("case", "change"),
    [
        ("missing", None),
        ("negative", {"overlap_seconds": -0.1}),
        ("nonfinite", {"speech_fraction": float("nan")}),
        ("probability_range", {"overlap_probability_mean": 1.01}),
        ("fraction_range", {"overlap_fraction": 1.01}),
        ("seconds_range", {"speech_seconds": 1.01}),
        (
            "overlap_exceeds_speech",
            {"overlap_seconds": 0.4, "speech_seconds": 0.3},
        ),
        (
            "overlap_fraction_exceeds_speech",
            {"overlap_fraction": 0.4, "speech_fraction": 0.3},
        ),
        ("simultaneous_type", {"max_simultaneous_speakers": 1.0}),
        ("simultaneous_range", {"max_simultaneous_speakers": 3}),
        ("window_range", {"max_window_speakers": 4}),
        ("empty_model", {"model": "   "}),
    ],
)
def test_malformed_overlap_evidence_fails_closed_before_decision(case, change):
    async def run():
        values = overlap_evidence()
        if case == "missing":
            del values["overlap_fraction"]
        else:
            assert change is not None
            values.update(change)

        class Analysis:
            def analyze(self, pcm):
                return SimpleNamespace(as_dict=lambda: values)

        store, output, models = EventStore(), Output(), Models("SPEAK")
        service = BenchRuntime(
            store,
            Asr(),
            models,
            output,
            vad_factory=lambda: None,
            overlap_detector=Analysis(),
        )

        await service.process_utterance(np.full(16000, 0.1, np.float32))

        assert output.spoken == [] and models.contexts == []
        assert service._awake_until is None
        stages = store.snapshot()["stages"]
        assert stages["quality"]["state"] == "rejected"
        assert stages["quality"]["details"]["evidence"]["overlap"] is None
        assert stages["decision"]["details"]["reason"] == "audio_analysis_unavailable"

    asyncio.run(run())


def test_failed_configured_audio_analysis_never_admits_a_reply():
    async def run():
        class Analysis:
            def analyze(self, pcm):
                raise RuntimeError("invalid segmentation output")

        store, output, models = EventStore(), Output(), Models("SPEAK")
        service = BenchRuntime(
            store,
            Asr(),
            models,
            output,
            vad_factory=lambda: None,
            overlap_detector=Analysis(),
        )
        await service.process_utterance(np.full(16000, 0.1, np.float32))
        assert output.spoken == [] and models.contexts == []
        assert service._awake_until is None
        assert (
            store.snapshot()["stages"]["decision"]["details"]["reason"]
            == "audio_analysis_unavailable"
        )

    asyncio.run(run())


def test_stop_during_audio_analysis_never_rearms_or_overlaps_inference():
    import threading

    async def run():
        entered, release = threading.Event(), threading.Event()
        calls = []

        class Analysis:
            def analyze(self, pcm):
                calls.append(1)
                entered.set()
                assert release.wait(3)
                return SimpleNamespace(
                    as_dict=lambda: overlap_evidence(speech_seconds=1.0)
                )

        store, output, models = EventStore(), Output(), Models("SPEAK")
        service = BenchRuntime(
            store,
            Asr(),
            models,
            output,
            vad_factory=lambda: None,
            overlap_detector=Analysis(),
        )
        try:
            first = service.turn_task = asyncio.create_task(
                service.process_utterance(np.full(16000, 0.1, np.float32))
            )
            assert await asyncio.to_thread(entered.wait, 1)
            await asyncio.wait_for(service.stop(), 0.5)
            assert first.cancelled()
            await service.process_utterance(np.full(16000, 0.1, np.float32))
            assert calls == [1]
            assert models.contexts == [] and output.spoken == []
            assert service._awake_until is None
            assert (
                store.snapshot()["stages"]["decision"]["details"]["reason"]
                == "audio_analysis_unavailable"
            )
        finally:
            release.set()
            if service._analysis_task:
                await service._analysis_task
        assert service._awake_until is None
        assert output.spoken == []

    asyncio.run(run())


def test_audible_replay_bypass_is_rejected():
    async def run():
        store, output, models = EventStore(), Output(), Models("SPEAK")
        service = BenchRuntime(store, Asr(), models, output, vad_factory=lambda: None)
        await service.process_utterance(
            np.full(16000, 0.1, np.float32), audible=True, _replay_wake_bypass=True
        )
        assert models.contexts == [] and output.spoken == []
        assert service._awake_until is None

    asyncio.run(run())
