import asyncio
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


class Store:
    def __init__(self):
        self.events = []

    def emit(self, stage, state, **details):
        event = dict(stage=stage, state=state, details=details)
        self.events.append(event)
        return event


class Output:
    def __init__(self):
        self.spoken = []
        self.spoken_languages = []
        self.stopped = False
        self.busy = False
        self.guard_until = 0

    async def stop(self):
        self.stopped = True

    async def warm(self):
        pass

    async def speak(self, text, generation, *, audible, language="English"):
        self.spoken.append(text)
        self.spoken_languages.append(language)


class FirstStopBlocks(Output):
    def __init__(self):
        super().__init__()
        self.first_stop_entered = asyncio.Event()
        self.release_first_stop = asyncio.Event()
        self.stop_calls = 0

    async def stop(self):
        self.stop_calls += 1
        if self.stop_calls == 1:
            self.first_stop_entered.set()
            await self.release_first_stop.wait()
        self.stopped = True


class Models:
    async def decision(
        self, text, history, *, addressed, active_conversation, evidence
    ):
        return "SPEAK" if addressed else "WAIT"

    async def reply(self, text, history, *, language="English"):
        yield "Hello. "
        yield "Nice to meet you."


def runtime(asr):
    from alice.conversation.runtime import BenchRuntime

    store, output = Store(), Output()
    service = BenchRuntime(store, asr, Models(), output, vad_factory=lambda: None)
    service.mode = "conversation"
    return service, store, output


def lifecycle_runtime(output):
    from alice.conversation.runtime import BenchRuntime

    class Asr:
        async def transcribe(self, pcm):
            if False:
                yield None

    store = Store()
    service = BenchRuntime(store, Asr(), Models(), output, vad_factory=lambda: None)
    return service, store


@pytest.mark.parametrize("mode", ["listen", "conversation"])
def test_start_enters_waiting_for_wake_in_both_modes(mode):
    async def run():
        output = Output()
        service, store = lifecycle_runtime(output)
        capture_started = asyncio.Event()

        async def fake_capture(*_args):
            capture_started.set()
            await asyncio.Event().wait()

        service._capture = fake_capture
        await service.start(mode)
        await asyncio.wait_for(capture_started.wait(), timeout=1)

        engagement = [event for event in store.events if event["stage"] == "engagement"]
        assert engagement[-1]["state"] == "waiting"
        assert "Alice" in engagement[-1]["details"]["message"]
        await service.stop()

    asyncio.run(run())


def test_no_speech_final_does_not_request_models_or_speaker():
    class Asr:
        async def transcribe(self, pcm):
            yield SimpleNamespace(text="", final=True)

    async def run():
        service, store, output = runtime(Asr())
        await service.process_utterance(np.zeros(8000, np.float32), audible=True)
        assert output.spoken == []
        assert service.history == []
        assert not [
            event
            for event in store.events
            if event["stage"] in {"decision", "llm", "tts"}
        ]
        assert store.events[-1]["stage"] == "asr"
        assert store.events[-1]["state"] == "final"
        assert store.events[-1]["details"]["text"] == ""

    asyncio.run(run())


def test_skipped_language_is_visible_without_reply_or_error():
    from alice.conversation.asr import Transcript

    class Asr:
        async def transcribe(self, pcm):
            yield Transcript(
                "",
                True,
                language="Arabic",
                ignored_reason="Skipped Arabic result; English mode",
            )

    async def run():
        service, store, output = runtime(Asr())
        await service.process_utterance(np.zeros(8000, np.float32), audible=True)
        event = store.events[-1]
        assert event["stage"] == "asr" and event["state"] == "ignored"
        assert event["details"]["language"] == "Arabic"
        assert event["details"]["message"] == "Skipped Arabic result; English mode"
        assert not [e for e in store.events if e["stage"] in {"decision", "llm", "tts"}]
        assert output.spoken == [] and service.history == []

    asyncio.run(run())


def test_final_transcript_commits_once_and_explicit_wake_is_admitted_by_decision():
    class Asr:
        async def transcribe(self, pcm):
            yield SimpleNamespace(text="Alice", final=False)
            yield SimpleNamespace(text=", hello", final=False)
            yield SimpleNamespace(text="Alice, hello", final=True)

    class RecordingModels(Models):
        def __init__(self):
            self.decisions = []

        async def decision(
            self, text, history, *, addressed, active_conversation, evidence
        ):
            self.decisions.append(
                (text, list(history), addressed, active_conversation, evidence)
            )
            return "SPEAK" if addressed else "WAIT"

    async def run():
        from alice.conversation.runtime import BenchRuntime

        store, output, models = Store(), Output(), RecordingModels()
        service = BenchRuntime(store, Asr(), models, output, vad_factory=lambda: None)
        service.mode = "conversation"
        await service.process_utterance(np.ones(512, np.float32), audible=False)
        assert output.spoken == ["Hello.", "Nice to meet you."]
        assert output.spoken_languages == ["English", "English"]
        partials = [
            x["details"]["text"]
            for x in store.events
            if x["stage"] == "asr" and x["state"] == "partial"
        ]
        assert partials == ["Alice", "Alice, hello"]
        assert (
            len(
                [
                    x
                    for x in store.events
                    if x["stage"] == "asr" and x["state"] == "final"
                ]
            )
            == 1
        )
        assert any(
            x["stage"] == "decision" and x["details"].get("text") == "SPEAK"
            for x in store.events
        )
        assert len(models.decisions) == 1
        text, history, addressed, active_conversation, evidence = models.decisions[0]
        assert (text, history, addressed, active_conversation) == (
            "Alice, hello",
            [],
            True,
            False,
        )
        assert evidence["language_tag"] == "English"
        assert evidence["voiced_seconds"] is None

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["listen", "conversation"])
@pytest.mark.parametrize(
    "text", ["We talked to Alice yesterday.", "Malice is not a wake name."]
)
def test_background_or_near_match_waits_without_any_model_or_tts_calls(mode, text):
    from alice.conversation.runtime import BenchRuntime

    class Asr:
        async def transcribe(self, pcm):
            yield SimpleNamespace(text=text, final=True)

    class NoModels:
        async def decision(
            self, text, history, *, addressed, active_conversation, evidence
        ):
            raise AssertionError("background speech must not request MiniCPM")

        async def reply(self, text, history, *, language="English"):
            raise AssertionError("background speech must not request remote reply")
            yield ""

    async def run():
        store, output = Store(), Output()
        service = BenchRuntime(
            store, Asr(), NoModels(), output, vad_factory=lambda: None
        )
        service.mode = mode

        await service.process_utterance(np.ones(512, np.float32), audible=True)

        decision = [event for event in store.events if event["stage"] == "decision"]
        assert decision[-1]["state"] == "advice"
        assert decision[-1]["details"]["text"] == "WAIT"
        assert decision[-1]["details"]["reason"] == "awaiting_wake"
        assert output.spoken == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "text",
    [
        "Alice",
        "Hello, Alice!",
        "你好，愛麗絲。",
        "喂爱丽丝，今日好嗎？",
        "愛麗絲你今日好嗎？",
        "爱丽丝你好",
    ],
)
def test_listen_mode_valid_wake_names_reply_after_decision_admission(text):
    from alice.conversation.runtime import BenchRuntime

    class Asr:
        async def transcribe(self, pcm):
            yield SimpleNamespace(text=text, final=True)

    class Models:
        def __init__(self):
            self.decisions = []
            self.replies = 0

        async def decision(
            self, text, history, *, addressed, active_conversation, evidence
        ):
            self.decisions.append((addressed, active_conversation, evidence))
            return "SPEAK" if addressed else "WAIT"

        async def reply(self, text, history, *, language="English"):
            self.replies += 1
            yield "Yes."

    async def run():
        store, output, models = Store(), Output(), Models()
        service = BenchRuntime(store, Asr(), models, output, vad_factory=lambda: None)
        service.mode = "listen"

        await service.process_utterance(np.ones(512, np.float32), audible=True)

        assert len(models.decisions) == 1
        addressed, active_conversation, evidence = models.decisions[0]
        assert addressed is True
        assert active_conversation is False
        assert evidence["language_tag"] == "English"
        assert models.replies == 1
        assert output.spoken == ["Yes."]
        engagement = [event for event in store.events if event["stage"] == "engagement"]
        assert engagement[-1]["state"] == "awake"
        assert engagement[-1]["details"]["remaining_seconds"] == 30.0

    asyncio.run(run())


@pytest.mark.parametrize(("advice", "expected_replies"), [("SPEAK", 2), ("WAIT", 1)])
def test_active_window_minicpm_advice_gates_followup(advice, expected_replies):
    from alice.conversation.runtime import BenchRuntime

    class Asr:
        def __init__(self):
            self.texts = iter(["Alice, hello.", "What about tomorrow?"])

        async def transcribe(self, pcm):
            yield SimpleNamespace(text=next(self.texts), final=True)

    class Models:
        def __init__(self):
            self.decision_calls = []
            self.reply_calls = 0

        async def decision(
            self, text, history, *, addressed, active_conversation, evidence
        ):
            self.decision_calls.append(
                (text, list(history), addressed, active_conversation, evidence)
            )
            return "SPEAK" if addressed else advice

        async def reply(self, text, history, *, language="English"):
            self.reply_calls += 1
            yield "Answer."

    async def run():
        store, output, models = Store(), Output(), Models()
        service = BenchRuntime(store, Asr(), models, output, vad_factory=lambda: None)
        service.mode = "conversation"

        await service.process_utterance(np.ones(512, np.float32))
        await service.process_utterance(np.ones(512, np.float32))

        assert models.reply_calls == expected_replies
        assert len(models.decision_calls) == 2
        initial, followup = models.decision_calls
        assert initial[:4] == ("Alice, hello.", [], True, False)
        assert followup[:4] == (
            "What about tomorrow?",
            [("Alice, hello.", "Answer.")],
            False,
            True,
        )
        assert initial[4]["language_tag"] == "English"
        assert followup[4]["language_tag"] == "English"
        assert len(output.spoken) == expected_replies

    asyncio.run(run())


def test_active_window_decision_error_suppresses_followup_reply():
    from alice.conversation.runtime import BenchRuntime

    class Asr:
        def __init__(self):
            self.texts = iter(["Alice.", "Background followup."])

        async def transcribe(self, pcm):
            yield SimpleNamespace(text=next(self.texts), final=True)

    class Models:
        def __init__(self):
            self.replies = 0

        async def decision(
            self, text, history, *, addressed, active_conversation, evidence
        ):
            if addressed:
                return "SPEAK"
            raise RuntimeError("decision unavailable")

        async def reply(self, text, history, *, language="English"):
            self.replies += 1
            yield "Acknowledged."

    async def run():
        store, output, models = Store(), Output(), Models()
        service = BenchRuntime(store, Asr(), models, output, vad_factory=lambda: None)
        service.mode = "conversation"

        await service.process_utterance(np.ones(512, np.float32))
        await service.process_utterance(np.ones(512, np.float32))

        assert models.replies == 1
        assert output.spoken == ["Acknowledged."]
        decision = [event for event in store.events if event["stage"] == "decision"]
        assert decision[-1]["state"] == "unavailable"

    asyncio.run(run())


def test_wake_window_expires_before_a_later_followup(monkeypatch: pytest.MonkeyPatch):
    import alice.conversation.runtime as runtime_module
    from alice.conversation.runtime import BenchRuntime

    class Clock:
        now = 100.0

        def __call__(self):
            return self.now

    class Asr:
        def __init__(self):
            self.texts = iter(["Alice, hello.", "Still there?"])

        async def transcribe(self, pcm):
            yield SimpleNamespace(text=next(self.texts), final=True)

    class Models:
        def __init__(self):
            self.decisions = 0
            self.replies = 0

        async def decision(
            self, text, history, *, addressed, active_conversation, evidence
        ):
            self.decisions += 1
            return "SPEAK" if addressed else "WAIT"

        async def reply(self, text, history, *, language="English"):
            self.replies += 1
            yield "Hello."

    async def run():
        clock = Clock()
        monkeypatch.setattr(runtime_module.time, "monotonic", clock)
        store, output, models = Store(), Output(), Models()
        service = BenchRuntime(store, Asr(), models, output, vad_factory=lambda: None)

        await service.process_utterance(np.ones(512, np.float32))
        clock.now += 31
        await service.process_utterance(np.ones(512, np.float32))

        assert models.decisions == 1
        assert models.replies == 1
        assert output.spoken == ["Hello."]
        engagement = [event for event in store.events if event["stage"] == "engagement"]
        assert engagement[-1]["state"] == "expired"
        decision = [event for event in store.events if event["stage"] == "decision"]
        assert decision[-1]["details"]["reason"] == "awaiting_wake"

    asyncio.run(run())


def test_followup_speak_advice_arriving_after_deadline_is_suppressed(
    monkeypatch: pytest.MonkeyPatch,
):
    import alice.conversation.runtime as runtime_module
    from alice.conversation.runtime import BenchRuntime

    async def run():
        class Clock:
            now = 100.0

            def __call__(self):
                return self.now

        class Asr:
            def __init__(self):
                self.texts = iter(["Alice, hello.", "And tomorrow?"])

            async def transcribe(self, pcm):
                yield SimpleNamespace(text=next(self.texts), final=True)

        decision_entered = asyncio.Event()
        release_decision = asyncio.Event()

        class Models:
            def __init__(self):
                self.replies = 0

            async def decision(
                self, text, history, *, addressed, active_conversation, evidence
            ):
                if addressed:
                    return "SPEAK"
                decision_entered.set()
                await release_decision.wait()
                return "SPEAK"

            async def reply(self, text, history, *, language="English"):
                self.replies += 1
                yield "Hello."

        clock = Clock()
        monkeypatch.setattr(runtime_module.time, "monotonic", clock)
        store, output, models = Store(), Output(), Models()
        service = BenchRuntime(store, Asr(), models, output, vad_factory=lambda: None)

        await service.process_utterance(np.ones(512, np.float32))
        clock.now = 120.0
        followup = asyncio.create_task(
            service.process_utterance(np.ones(512, np.float32))
        )
        await asyncio.wait_for(decision_entered.wait(), timeout=1)
        clock.now = 131.0
        release_decision.set()
        await asyncio.wait_for(followup, timeout=1)

        assert models.replies == 1
        assert output.spoken == ["Hello."]
        engagement = [event for event in store.events if event["stage"] == "engagement"]
        assert engagement[-1]["state"] == "expired"
        decision = [event for event in store.events if event["stage"] == "decision"]
        assert decision[-1]["details"]["text"] == "WAIT"
        assert decision[-1]["details"]["reason"] == "wake_expired"

    asyncio.run(run())


def test_wake_window_expires_while_capture_continues(
    monkeypatch: pytest.MonkeyPatch,
):
    import alice.conversation.runtime as runtime_module
    from alice.conversation.runtime import BenchRuntime

    async def run():
        class Clock:
            now = 100.0

            def __call__(self):
                return self.now

        expired = asyncio.Event()

        class SignalingStore(Store):
            def emit(self, stage, state, **details):
                event = super().emit(stage, state, **details)
                if stage == "engagement" and state == "expired":
                    expired.set()
                return event

        first_frame = asyncio.Event()
        release_second = asyncio.Event()

        async def routes():
            return "input", "output"

        async def frames(_source):
            yield np.zeros(512, np.float32)
            first_frame.set()
            await release_second.wait()
            yield np.zeros(512, np.float32)
            await asyncio.Event().wait()

        class Vad:
            def probability(self, frame):
                return 0.0

        class Asr:
            async def transcribe(self, pcm):
                if False:
                    yield None

        clock = Clock()
        monkeypatch.setattr(runtime_module.time, "monotonic", clock)
        monkeypatch.setattr(runtime_module, "inspect_routes", routes)
        monkeypatch.setattr(runtime_module, "capture", frames)
        store, output = SignalingStore(), Output()
        service = BenchRuntime(store, Asr(), Models(), output, vad_factory=Vad)

        await service.start("listen")
        await asyncio.wait_for(first_frame.wait(), timeout=1)
        service._open_wake_window("test wake")
        clock.now += 31
        release_second.set()
        await asyncio.wait_for(expired.wait(), timeout=1)

        engagement = [event for event in store.events if event["stage"] == "engagement"]
        assert engagement[-1]["state"] == "expired"
        await service.stop()

    asyncio.run(run())


def test_stop_prevents_late_output_completion_from_rearming_wake_window():
    from alice.conversation.runtime import BenchRuntime

    async def run():
        speak_entered = asyncio.Event()
        cancellation_seen = asyncio.Event()
        release_speak = asyncio.Event()

        class Asr:
            async def transcribe(self, pcm):
                yield SimpleNamespace(text="Alice, hello.", final=True)

        class ReplyModels:
            async def decision(
                self, text, history, *, addressed, active_conversation, evidence
            ):
                return "SPEAK" if addressed else "WAIT"

            async def reply(self, text, history, *, language="English"):
                yield "Hello."

        class SlowOutput(Output):
            async def speak(self, text, generation, *, audible, language="English"):
                speak_entered.set()
                try:
                    await release_speak.wait()
                except asyncio.CancelledError:
                    cancellation_seen.set()
                    await release_speak.wait()

        store, output = Store(), SlowOutput()
        service = BenchRuntime(
            store, Asr(), ReplyModels(), output, vad_factory=lambda: None
        )
        turn = asyncio.create_task(
            service.process_utterance(np.ones(512, np.float32), audible=True)
        )
        service.turn_task = turn
        await asyncio.wait_for(speak_entered.wait(), timeout=1)

        stopping = asyncio.create_task(service.stop())
        await asyncio.wait_for(cancellation_seen.wait(), timeout=1)
        assert service.history == []
        release_speak.set()
        await asyncio.wait_for(stopping, timeout=1)

        engagement = [event for event in store.events if event["stage"] == "engagement"]
        cancelled_index = max(
            index
            for index, event in enumerate(engagement)
            if event["state"] == "cancelled"
        )
        assert not any(
            event["state"] == "awake" for event in engagement[cancelled_index + 1 :]
        )
        assert service.history == []
        assert turn.done()

    asyncio.run(run())


def test_cantonese_language_reaches_reply_and_each_cjk_speech_clause():
    from alice.conversation.asr import Transcript
    from alice.conversation.runtime import BenchRuntime

    class Asr:
        async def transcribe(self, pcm):
            yield Transcript("你好愛麗絲", True, language="Cantonese")

    class RecordingModels:
        def __init__(self):
            self.reply_languages = []

        async def decision(
            self, text, history, *, addressed, active_conversation, evidence
        ):
            return "SPEAK"

        async def reply(self, text, history, *, language="English"):
            self.reply_languages.append(language)
            yield "你好。再見！"

    async def run():
        store, output, models = Store(), Output(), RecordingModels()
        service = BenchRuntime(store, Asr(), models, output, vad_factory=lambda: None)
        service.mode = "conversation"

        await service.process_utterance(np.ones(512, np.float32), audible=True)

        assert models.reply_languages == ["Cantonese"]
        assert output.spoken == ["你好。", "再見！"]
        assert output.spoken_languages == ["Cantonese", "Cantonese"]
        assert service.history == [("你好愛麗絲", "你好。再見！")]
        final = [
            event
            for event in store.events
            if event["stage"] == "asr" and event["state"] == "final"
        ]
        assert final[0]["details"]["language"] == "Cantonese"

    asyncio.run(run())


def test_chinese_asr_tag_stays_visible_but_routes_as_cantonese():
    from alice.conversation.asr import Transcript
    from alice.conversation.runtime import BenchRuntime

    class Asr:
        async def transcribe(self, pcm):
            yield Transcript("愛麗絲，你今日想聽啲咩", True, language="Chinese")

    class RecordingModels:
        def __init__(self):
            self.language = None

        async def decision(
            self, text, history, *, addressed, active_conversation, evidence
        ):
            return "SPEAK"

        async def reply(self, text, history, *, language="English"):
            self.language = language
            yield "我想聽歌。"

    async def run():
        store, output, models = Store(), Output(), RecordingModels()
        service = BenchRuntime(store, Asr(), models, output, vad_factory=lambda: None)
        service.mode = "conversation"

        await service.process_utterance(np.ones(512, np.float32), audible=True)

        final = [
            event
            for event in store.events
            if event["stage"] == "asr" and event["state"] == "final"
        ]
        assert final[0]["details"]["language"] == "Chinese"
        assert models.language == "Cantonese"
        assert output.spoken_languages == ["Cantonese"]

    asyncio.run(run())


def test_stop_invalidates_late_asr_and_stops_output_first():
    async def run():
        entered = asyncio.Event()
        release = asyncio.Event()

        class Asr:
            async def transcribe(self, pcm):
                entered.set()
                await release.wait()
                yield SimpleNamespace(text="Obsolete words", final=True)

        service, store, output = runtime(Asr())
        task = asyncio.create_task(service.process_utterance(np.ones(512, np.float32)))
        await entered.wait()
        await service.stop()
        assert output.stopped
        release.set()
        await task
        assert not output.spoken
        assert not any(
            x["stage"] == "asr" and x["state"] == "final" for x in store.events
        )
        cancelled = [
            x for x in store.events if x["stage"] == "asr" and x["state"] == "cancelled"
        ]
        assert [x["details"]["generation_id"] for x in cancelled] == [
            service.identity(0)
        ]

    asyncio.run(run())


def test_failed_asr_does_not_call_models_or_play_partial_text():
    class Asr:
        async def transcribe(self, pcm):
            yield SimpleNamespace(text="Incomplete", final=False)
            raise RuntimeError("disconnected")

    async def run():
        service, store, output = runtime(Asr())
        await service.process_utterance(np.ones(512, np.float32))
        assert not output.spoken
        assert any(x["stage"] == "asr" and x["state"] == "error" for x in store.events)

    asyncio.run(run())


def test_duplicate_final_is_rejected_before_reply():
    class Asr:
        async def transcribe(self, pcm):
            yield SimpleNamespace(text="Hello", final=True)
            yield SimpleNamespace(text="Changed", final=True)

    async def run():
        service, store, output = runtime(Asr())
        await service.process_utterance(np.ones(512, np.float32))
        assert not output.spoken
        assert any(x["stage"] == "asr" and x["state"] == "error" for x in store.events)

    asyncio.run(run())


def test_replay_utterances_have_distinct_generation_ids(tmp_path):
    import wave

    audio = np.concatenate(
        [
            np.full(512 * 3, 29000, dtype="<i2"),
            np.zeros(512 * 12, dtype="<i2"),
            np.full(512 * 3, 29000, dtype="<i2"),
            np.zeros(512 * 12, dtype="<i2"),
        ]
    )
    path = tmp_path / "synthetic.wav"
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(audio.tobytes())

    class Asr:
        async def transcribe(self, pcm):
            yield SimpleNamespace(text="Hello", final=True)

    class Vad:
        def probability(self, frame):
            return float(frame[0])

    async def run():
        service, store, _ = runtime(Asr())
        service.replay_path = path
        service.vad_factory = Vad
        await service._replay()
        identities = [
            x["details"]["generation_id"]
            for x in store.events
            if x["stage"] == "asr" and x["state"] == "final"
        ]
        assert len(identities) == 2
        assert len(set(identities)) == 2
        replay_decisions = [
            event
            for event in store.events
            if event["stage"] == "decision"
            and event["details"].get("reason") == "replay_wake"
        ]
        assert len(replay_decisions) == 2

    asyncio.run(run())


def test_stop_invalidates_an_earlier_start_before_it_can_create_capture():
    async def run():
        output = FirstStopBlocks()
        service, _ = lifecycle_runtime(output)
        capture_started = asyncio.Event()

        async def fake_capture(*_args):
            capture_started.set()
            await asyncio.Event().wait()

        service._capture = fake_capture
        starting = asyncio.create_task(service.start("listen"))
        await asyncio.wait_for(output.first_stop_entered.wait(), timeout=1)

        stopping = asyncio.create_task(service.stop())
        await asyncio.wait_for(stopping, timeout=1)
        output.release_first_stop.set()
        await asyncio.wait_for(starting, timeout=1)
        await asyncio.sleep(0)

        assert service.session_task is None
        assert not capture_started.is_set()

    asyncio.run(run())


def test_overlapping_starts_create_only_the_latest_capture():
    async def run():
        output = FirstStopBlocks()
        service, _ = lifecycle_runtime(output)
        captures: list[str] = []

        async def fake_capture(*_args):
            captures.append(service.mode)
            await asyncio.Event().wait()

        service._capture = fake_capture
        first = asyncio.create_task(service.start("listen"))
        await asyncio.wait_for(output.first_stop_entered.wait(), timeout=1)
        await service.start("conversation")
        await asyncio.sleep(0)
        output.release_first_stop.set()
        await asyncio.wait_for(first, timeout=1)
        await asyncio.sleep(0)

        assert captures == ["conversation"]
        assert service.mode == "conversation"
        assert service.session_task is not None
        assert not service.session_task.done()
        await service.stop()

    asyncio.run(run())


def test_stop_invalidates_an_earlier_replay_before_it_can_create_task():
    async def run():
        output = FirstStopBlocks()
        service, _ = lifecycle_runtime(output)
        service.replay_path = Path("synthetic.wav")
        replay_started = asyncio.Event()

        async def fake_replay(*_args):
            replay_started.set()

        service._replay = fake_replay
        replaying = asyncio.create_task(service.replay())
        await asyncio.wait_for(output.first_stop_entered.wait(), timeout=1)
        await asyncio.wait_for(service.stop(), timeout=1)
        output.release_first_stop.set()
        await asyncio.wait_for(replaying, timeout=1)
        await asyncio.sleep(0)

        assert service.session_task is None
        assert not replay_started.is_set()

    asyncio.run(run())


def test_later_start_remains_active_after_an_earlier_stop_resumes():
    async def run():
        output = FirstStopBlocks()
        service, _ = lifecycle_runtime(output)
        capture_started = asyncio.Event()

        async def fake_capture(*_args):
            capture_started.set()
            await asyncio.Event().wait()

        service._capture = fake_capture
        stopping = asyncio.create_task(service.stop())
        await asyncio.wait_for(output.first_stop_entered.wait(), timeout=1)
        await service.start("listen")
        await asyncio.wait_for(capture_started.wait(), timeout=1)
        output.release_first_stop.set()
        await asyncio.wait_for(stopping, timeout=1)

        assert service.session_task is not None
        assert not service.session_task.done()
        await service.stop()

    asyncio.run(run())


def test_completed_capture_cleans_up_without_waiting_on_itself(
    monkeypatch: pytest.MonkeyPatch,
):
    import alice.conversation.runtime as runtime_module

    async def run():
        output = Output()
        service, _ = lifecycle_runtime(output)

        async def routes():
            return "input", "output"

        async def no_frames(_source):
            if False:
                yield np.zeros(512, np.float32)

        monkeypatch.setattr(runtime_module, "inspect_routes", routes)
        monkeypatch.setattr(runtime_module, "capture", no_frames)
        await service.start("listen")
        task = service.session_task
        assert task is not None
        await asyncio.wait_for(task, timeout=1)
        assert service.session_task is None

    asyncio.run(run())


def test_stop_retires_active_reply_and_tts_but_preserves_asr_final():
    async def run():
        reply_active = asyncio.Event()
        tts_active = asyncio.Event()

        class Asr:
            async def transcribe(self, pcm):
                yield SimpleNamespace(text="Alice, hello", final=True)

        class BlockingModels:
            async def decision(
                self, text, history, *, addressed, active_conversation, evidence
            ):
                return "SPEAK" if addressed else "WAIT"

            async def reply(self, text, history, *, language="English"):
                yield "Hello. "
                reply_active.set()
                await asyncio.Event().wait()

        class BlockingOutput(Output):
            async def speak(self, text, generation, *, audible, language="English"):
                tts_active.set()
                await asyncio.Event().wait()

        from alice.conversation.runtime import BenchRuntime

        store, output = Store(), BlockingOutput()
        service = BenchRuntime(
            store, Asr(), BlockingModels(), output, vad_factory=lambda: None
        )
        service.mode = "conversation"
        task = asyncio.create_task(
            service.process_utterance(np.ones(512, np.float32), audible=False)
        )
        service.turn_task = task
        await asyncio.wait_for(reply_active.wait(), timeout=1)
        await asyncio.wait_for(tts_active.wait(), timeout=1)

        await service.stop()
        latest = {event["stage"]: event for event in store.events}

        assert latest["asr"]["state"] == "final"
        for stage in ("llm", "tts"):
            assert latest[stage]["state"] == "cancelled"
            assert latest[stage]["details"]["generation_id"] == service.identity(0)
        assert latest["engagement"]["state"] == "cancelled"
        assert task.done()

    asyncio.run(run())


def test_stop_retires_an_active_followup_decision():
    from alice.conversation.runtime import BenchRuntime

    async def run():
        decision_active = asyncio.Event()

        class Asr:
            def __init__(self):
                self.texts = iter(["Alice, hello.", "And tomorrow?"])

            async def transcribe(self, pcm):
                yield SimpleNamespace(text=next(self.texts), final=True)

        class BlockingModels:
            async def decision(
                self, text, history, *, addressed, active_conversation, evidence
            ):
                if addressed:
                    return "SPEAK"
                decision_active.set()
                await asyncio.Event().wait()

            async def reply(self, text, history, *, language="English"):
                yield "Hello."

        store, output = Store(), Output()
        service = BenchRuntime(
            store, Asr(), BlockingModels(), output, vad_factory=lambda: None
        )
        await service.process_utterance(np.ones(512, np.float32))
        followup = asyncio.create_task(
            service.process_utterance(np.ones(512, np.float32))
        )
        service.turn_task = followup
        await asyncio.wait_for(decision_active.wait(), timeout=1)

        await service.stop()
        latest = {event["stage"]: event for event in store.events}

        assert latest["decision"]["state"] == "cancelled"
        assert latest["engagement"]["state"] == "cancelled"
        assert followup.done()

    asyncio.run(run())


def test_stop_retires_active_vad_state_with_generation_identity(
    monkeypatch: pytest.MonkeyPatch,
):
    import alice.conversation.runtime as runtime_module

    async def run():
        vad_called = asyncio.Event()
        hold_source = asyncio.Event()

        class Vad:
            def probability(self, frame):
                vad_called.set()
                return 1.0

        async def routes():
            return "input", "output"

        async def frames(_source):
            yield np.ones(512, np.float32)
            await hold_source.wait()

        monkeypatch.setattr(runtime_module, "inspect_routes", routes)
        monkeypatch.setattr(runtime_module, "capture", frames)
        output = Output()
        service, store = lifecycle_runtime(output)
        service.vad_factory = Vad
        await service.start("listen")
        await asyncio.wait_for(vad_called.wait(), timeout=1)
        await asyncio.sleep(0)

        await service.stop()
        hold_source.set()
        latest = {event["stage"]: event for event in store.events}

        assert latest["vad"]["state"] == "stopped"
        assert latest["vad"]["details"]["generation_id"] == service.identity(1)

    asyncio.run(run())


def test_capture_backlog_fault_remains_visible_after_session_cleanup(
    monkeypatch: pytest.MonkeyPatch,
):
    import alice.conversation.runtime as runtime_module

    async def run():
        async def routes():
            return "input", "output"

        async def failed_frames(_source):
            if False:
                yield np.zeros(512, np.float32)
            raise RuntimeError("microphone capture queue overflow")

        monkeypatch.setattr(runtime_module, "inspect_routes", routes)
        monkeypatch.setattr(runtime_module, "capture", failed_frames)
        output = Output()
        service, store = lifecycle_runtime(output)
        await service.start("listen")
        task = service.session_task
        assert task is not None
        await asyncio.wait_for(task, timeout=1)
        latest = {event["stage"]: event for event in store.events}

        assert latest["capture"]["state"] == "error"
        assert "queue overflow" in latest["capture"]["details"]["error"]
        assert latest["session"]["state"] == "error"

    asyncio.run(run())


def test_capture_discards_overlong_speech_then_processes_next_endpoint_with_evidence(
    monkeypatch: pytest.MonkeyPatch,
):
    import alice.conversation.runtime as runtime_module
    from alice.conversation.runtime import BenchRuntime

    async def run():
        processed = asyncio.Event()
        hold_source = asyncio.Event()
        utterances = []

        async def routes():
            return "input", "output"

        async def frames(_source):
            for _ in range(469):
                yield np.full(512, 0.9, np.float32)
            for _ in range(10):
                yield np.zeros(512, np.float32)
            for _ in range(3):
                yield np.full(512, 0.9, np.float32)
            for _ in range(10):
                yield np.zeros(512, np.float32)
            await hold_source.wait()

        class Vad:
            def probability(self, frame):
                return 0.9 if frame[0] else 0.01

        class Asr:
            async def transcribe(self, pcm):
                raise AssertionError("capture handoff is replaced in this regression")
                yield None

        async def process_utterance(pcm, *, audible=False, vad_evidence=None, **_):
            utterances.append((pcm.copy(), audible, vad_evidence))
            processed.set()

        monkeypatch.setattr(runtime_module, "inspect_routes", routes)
        monkeypatch.setattr(runtime_module, "capture", frames)
        store, output = Store(), Output()
        service = BenchRuntime(store, Asr(), Models(), output, vad_factory=Vad)
        service.process_utterance = process_utterance

        await service.start("listen")
        try:
            await asyncio.wait_for(processed.wait(), timeout=1)

            assert service.session_task is not None
            assert not service.session_task.done()
            assert len(utterances) == 1
            pcm, audible, evidence = utterances[0]
            assert pcm.shape == (13 * 512,)
            assert audible is True
            assert evidence == pytest.approx(
                {
                    "speech_probability_mean": (3 * 0.9 + 10 * 0.01) / 13,
                    "voiced_fraction": 3 / 13,
                    "voiced_seconds": 3 * 512 / 16000,
                }
            )
            discarded = [
                event
                for event in store.events
                if event["stage"] == "decision"
                and event["details"].get("reason") == "overlong_utterance"
            ]
            assert len(discarded) == 1
            assert discarded[0]["state"] == "advice"
            assert discarded[0]["details"]["text"] == "WAIT"
            assert not [event for event in store.events if event["state"] == "error"]
        finally:
            await service.stop()
            hold_source.set()

    asyncio.run(run())


def test_turn_is_cancelled_even_when_immediate_output_stop_fails():
    async def run():
        turn_cancelled = asyncio.Event()

        class FailingOutput(Output):
            async def stop(self):
                raise RuntimeError("output stop failed")

        async def turn():
            try:
                await asyncio.Event().wait()
            finally:
                turn_cancelled.set()

        service, _ = lifecycle_runtime(FailingOutput())
        task = asyncio.create_task(turn())
        service.turn_task = task
        await asyncio.sleep(0)

        with pytest.raises(RuntimeError, match="output stop failed"):
            await service.stop()

        await asyncio.wait_for(turn_cancelled.wait(), timeout=1)
        assert task.done()
        assert service.turn_task is None

    asyncio.run(run())


def test_stale_capture_finalizer_cannot_overwrite_a_new_listening_session(
    monkeypatch: pytest.MonkeyPatch,
):
    import alice.conversation.runtime as runtime_module

    async def run():
        finalizer_stop_entered = asyncio.Event()
        release_finalizer_stop = asyncio.Event()

        class FinalizerBlockingOutput(Output):
            def __init__(self):
                super().__init__()
                self.stop_calls = 0

            async def stop(self):
                self.stop_calls += 1
                if self.stop_calls == 2:
                    finalizer_stop_entered.set()
                    await release_finalizer_stop.wait()
                self.stopped = True

        capture_calls = 0
        new_capture_entered = asyncio.Event()
        hold_new_capture = asyncio.Event()

        async def routes():
            return "input", "output"

        async def frames(_source):
            nonlocal capture_calls
            capture_calls += 1
            if capture_calls == 1:
                if False:
                    yield np.zeros(512, np.float32)
                return
            new_capture_entered.set()
            await hold_new_capture.wait()
            if False:
                yield np.zeros(512, np.float32)

        monkeypatch.setattr(runtime_module, "inspect_routes", routes)
        monkeypatch.setattr(runtime_module, "capture", frames)
        output = FinalizerBlockingOutput()
        service, store = lifecycle_runtime(output)
        await service.start("listen")
        await asyncio.wait_for(finalizer_stop_entered.wait(), timeout=1)

        await service.start("listen")
        new_task = service.session_task
        assert new_task is not None
        await asyncio.wait_for(new_capture_entered.wait(), timeout=1)
        release_finalizer_stop.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        latest = {event["stage"]: event for event in store.events}
        assert service.session_task is new_task
        assert not new_task.done()
        assert latest["session"]["state"] == "listening"

        await service.stop()
        hold_new_capture.set()

    asyncio.run(run())


def test_successful_real_speaker_tts_has_a_terminal_stage():
    from alice.conversation.audio import Speaker
    from alice.conversation.runtime import BenchRuntime

    async def run():
        class Asr:
            async def transcribe(self, pcm):
                yield SimpleNamespace(text="Alice, hello", final=True)

        class SuccessfulModels:
            async def decision(
                self, text, history, *, addressed, active_conversation, evidence
            ):
                return "SPEAK" if addressed else "WAIT"

            async def reply(self, text, history, *, language="English"):
                yield "Hello."

        class Worker:
            async def stream(self, clause):
                yield SimpleNamespace(pcm=np.zeros(20, np.float32), sample_rate=24_000)

            async def cancel(self, generation):
                pass

        store = Store()
        speaker = Speaker(store.emit)
        speaker.worker = Worker()
        service = BenchRuntime(
            store, Asr(), SuccessfulModels(), speaker, vad_factory=lambda: None
        )
        service.mode = "conversation"

        await service.process_utterance(np.ones(512, np.float32), audible=False)
        await service.stop()
        latest = {event["stage"]: event for event in store.events}

        assert latest["tts"]["state"] == "complete"
        assert latest["tts"]["details"]["generation_id"] == service.identity(0)

    asyncio.run(run())


def test_reply_failure_does_not_refresh_the_wake_window():
    from alice.conversation.runtime import BenchRuntime

    async def run():
        class Asr:
            async def transcribe(self, pcm):
                yield SimpleNamespace(text="Alice, hello", final=True)

        class FailingModels:
            async def decision(
                self, text, history, *, addressed, active_conversation, evidence
            ):
                return "SPEAK" if addressed else "WAIT"

            async def reply(self, text, history, *, language="English"):
                if False:
                    yield ""
                raise RuntimeError("reply failed")

        store, output = Store(), Output()
        service = BenchRuntime(
            store, Asr(), FailingModels(), output, vad_factory=lambda: None
        )
        service.mode = "conversation"

        await service.process_utterance(np.ones(512, np.float32), audible=False)
        await service.stop()
        latest = {event["stage"]: event for event in store.events}

        assert latest["llm"]["state"] == "error"
        awake = [event for event in store.events if event["stage"] == "engagement"]
        assert [event["state"] for event in awake].count("awake") == 1

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["listen", "conversation"])
def test_slow_reply_survives_new_speech_without_blocking_capture(monkeypatch, mode):
    """Unrequested interruption must not await worker shutdown in the capture loop."""
    import alice.conversation.runtime as runtime_module
    from alice.conversation.audio import buffered_frames
    from alice.conversation.runtime import BenchRuntime

    async def run():
        generating = asyncio.Event()
        release_reply = asyncio.Event()
        all_frames_processed = asyncio.Event()
        allow_next_utterance = asyncio.Event()
        next_reply = asyncio.Event()
        never = asyncio.Event()

        class Asr:
            calls = 0

            async def transcribe(self, pcm):
                self.calls += 1
                language = "Cantonese" if self.calls == 1 else "English"
                text = "愛麗絲你好" if self.calls == 1 else "Alice, hello."
                yield SimpleNamespace(text=text, final=True, language=language)

        class SlowOutput(Output):
            active = False

            async def speak(self, text, generation, *, audible, language="English"):
                self.active = True
                try:
                    if language == "Cantonese":
                        generating.set()
                        await release_reply.wait()
                    self.spoken_languages.append(language)
                    if language == "English":
                        next_reply.set()
                finally:
                    self.active = False

            async def stop(self):
                if self.active:
                    await asyncio.sleep(0.2)  # Worker shutdown exceeds capture queue.

        class OneSentenceModels(Models):
            async def reply(self, text, history, *, language="English"):
                yield "你好呀。" if language == "Cantonese" else "Hello."

        class Vad:
            count = 0

            def probability(self, frame):
                self.count += 1
                if self.count == 46:
                    all_frames_processed.set()
                return float(frame[0])

        async def routes():
            return "input", "output"

        async def raw_frames():
            for probability in [1.0] * 3 + [0.0] * 10:
                yield np.full(512, probability, np.float32)
                await asyncio.sleep(0.002)
            await generating.wait()
            for probability in [1.0] * 3 + [0.0] * 30:
                yield np.full(512, probability, np.float32)
                await asyncio.sleep(0.002)
            await allow_next_utterance.wait()
            for probability in [1.0] * 3 + [0.0] * 10:
                yield np.full(512, probability, np.float32)
                await asyncio.sleep(0.002)
            await never.wait()

        async def frames(_source):
            stream = buffered_frames(raw_frames())
            try:
                async for frame in stream:
                    yield frame
            finally:
                await stream.aclose()

        monkeypatch.setattr(runtime_module, "inspect_routes", routes)
        monkeypatch.setattr(runtime_module, "capture", frames)
        store, output, asr = Store(), SlowOutput(), Asr()
        service = BenchRuntime(store, asr, OneSentenceModels(), output, vad_factory=Vad)
        waiter = asyncio.create_task(all_frames_processed.wait())
        try:
            await service.start(mode)
            await asyncio.wait_for(generating.wait(), 1)
            first_turn = service.turn_task
            assert first_turn is not None
            await asyncio.wait(
                {waiter, service.session_task},
                timeout=1,
                return_when=asyncio.FIRST_COMPLETED,
            )
            assert all_frames_processed.is_set(), store.events[-5:]
            assert service.turn_task is first_turn and not first_turn.done()
            assert asr.calls == 1
            release_reply.set()
            await asyncio.wait_for(asyncio.shield(first_turn), 1)
            allow_next_utterance.set()
            await asyncio.wait_for(next_reply.wait(), 1)
            assert output.spoken_languages == ["Cantonese", "English"]
            assert not [e for e in store.events if e["state"] == "error"]
        finally:
            release_reply.set()
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
            await service.stop()

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["listen", "conversation"])
def test_live_modes_warm_speech_before_opening_microphone(monkeypatch, mode):
    import alice.conversation.runtime as runtime_module

    async def run():
        warmed = False
        opened = False

        class WarmOutput(Output):
            async def warm(self):
                nonlocal warmed
                warmed = True

        async def routes():
            return "input", "output"

        async def frames(_source):
            nonlocal opened
            assert warmed, "microphone opened before voices warmed"
            opened = True
            if False:
                yield np.zeros(512, np.float32)

        monkeypatch.setattr(runtime_module, "inspect_routes", routes)
        monkeypatch.setattr(runtime_module, "capture", frames)
        service, store = lifecycle_runtime(WarmOutput())
        await service.start(mode)
        await asyncio.wait_for(service.session_task, 1)
        assert opened, store.events
        assert not [e for e in store.events if e["state"] == "error"]

    asyncio.run(run())
