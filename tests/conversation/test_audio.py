import asyncio
from collections.abc import AsyncIterator

import numpy as np
import pytest


def test_pcm_output_enforces_accepted_peak_without_changing_channel_count():
    from alice.conversation.audio import capped_pcm

    values = np.array([-1, -0.5, 0, 0.5, 1], np.float32)
    result = np.frombuffer(capped_pcm(values), dtype="<f4")
    assert len(result) == 5
    assert max(abs(result)) <= 11572 / 32768
    np.testing.assert_allclose(result, values * (11572 / 32768))


@pytest.mark.parametrize("pcm", [np.array([np.nan]), np.ones((2, 2)), np.array([2.0])])
def test_invalid_output_is_rejected_before_playback(pcm):
    from alice.conversation.audio import capped_pcm

    with pytest.raises(ValueError):
        capped_pcm(pcm)


def test_route_selection_rejects_monitor_and_ambiguous_devices():
    from alice.conversation.audio import select_route

    prefix = "alsa_input.usb-Seeed_Studio_ReSpeaker_Lite_0000000001-00."
    assert select_route([{"name": prefix + "analog-stereo"}], "input").endswith(
        "analog-stereo"
    )
    for items in [
        [],
        [{"name": prefix + "analog-stereo.monitor"}],
        [{"name": prefix + "a"}, {"name": prefix + "b"}],
    ]:
        with pytest.raises(RuntimeError):
            select_route(items, "input")


def test_pipewire_parser_selects_only_directional_audio_nodes():
    from alice.conversation.audio import select_pipewire_routes

    source = "alsa_input.usb-Seeed_Studio_ReSpeaker_Lite_0000000001-00.iec958-stereo"
    sink = "alsa_output.usb-Seeed_Studio_ReSpeaker_Lite_0000000001-00.iec958-stereo"

    def item(interface, media_class, name):
        return {
            "type": interface,
            "info": {"props": {"media.class": media_class, "node.name": name}},
        }

    payload = [
        item("PipeWire:Interface:Device", "Audio/Source", source + ".device"),
        item("PipeWire:Interface:Port", "Audio/Source", source + ".port"),
        item("PipeWire:Interface:Node", "Audio/Device", source + ".device-node"),
        item("PipeWire:Interface:Node", "Audio/Sink", source + ".wrong-direction"),
        item("PipeWire:Interface:Node", "Audio/Source", source + ".monitor"),
        item("PipeWire:Interface:Node", "Audio/Source", source),
        item("PipeWire:Interface:Node", "Audio/Sink", sink),
    ]

    assert select_pipewire_routes(payload) == (source, sink)


def test_buffered_capture_faults_before_delivering_frames_after_overflow():
    from alice.conversation.audio import buffered_frames

    async def run():
        overflow_attempted = asyncio.Event()
        source_closed = asyncio.Event()
        release_after_first = asyncio.Event()

        async def source() -> AsyncIterator[np.ndarray]:
            try:
                yield np.zeros(512, np.float32)
                await release_after_first.wait()
                for index in range(4):
                    if index == 2:
                        overflow_attempted.set()
                    yield np.full(512, index + 1, np.float32)
            finally:
                source_closed.set()

        stream = buffered_frames(source(), max_frames=2, max_age_s=1)
        first = await anext(stream)
        np.testing.assert_array_equal(first, np.zeros(512, np.float32))
        release_after_first.set()
        await asyncio.wait_for(overflow_attempted.wait(), timeout=1)
        await asyncio.sleep(0)

        with pytest.raises(RuntimeError, match="capture queue overflow"):
            await anext(stream)

        await stream.aclose()
        assert source_closed.is_set()

    asyncio.run(run())


def test_buffered_capture_faults_instead_of_delivering_a_stale_frame():
    from alice.conversation.audio import buffered_frames

    async def run():
        second_queued = asyncio.Event()
        release = asyncio.Event()

        async def source() -> AsyncIterator[np.ndarray]:
            yield np.zeros(512, np.float32)
            second_queued.set()
            yield np.ones(512, np.float32)
            await release.wait()

        stream = buffered_frames(source(), max_frames=2, max_age_s=0.01)
        await anext(stream)
        await asyncio.wait_for(second_queued.wait(), timeout=1)
        await asyncio.sleep(0.02)

        with pytest.raises(RuntimeError, match="stale"):
            await anext(stream)

        release.set()
        await stream.aclose()

    asyncio.run(run())


def test_stale_speaker_stop_cannot_cancel_or_clear_a_new_warm_owner(
    monkeypatch: pytest.MonkeyPatch,
):
    import alice.conversation.audio as audio_module
    from alice.conversation.audio import Speaker

    async def run():
        old_process = object()
        old_terminate_entered = asyncio.Event()
        release_old_terminate = asyncio.Event()
        events = []

        class Worker:
            def __init__(self):
                self.cancelled = []

            async def cancel(self, generation):
                self.cancelled.append(generation)

        async def gated_terminate(process):
            if process is old_process:
                old_terminate_entered.set()
                await release_old_terminate.wait()

        monkeypatch.setattr(audio_module, "terminate", gated_terminate)
        speaker = Speaker(lambda stage, state, **details: events.append((stage, state)))
        worker = Worker()
        speaker.worker = worker
        speaker.process = old_process
        speaker.active = "old-turn"
        speaker.busy = True

        stale_stop = asyncio.create_task(speaker.stop())
        await asyncio.wait_for(old_terminate_entered.wait(), timeout=1)
        await speaker.stop()
        speaker.active = "new-session-warm"

        release_old_terminate.set()
        await asyncio.wait_for(stale_stop, timeout=1)

        assert worker.cancelled == ["old-turn"]
        assert speaker.active == "new-session-warm"
        assert events == [("output", "stopped")]

    asyncio.run(run())


def test_stale_speak_cleanup_cannot_clear_new_speaker_ownership(
    monkeypatch: pytest.MonkeyPatch,
):
    import alice.conversation.audio as audio_module
    from alice.conversation.audio import Speaker

    async def run():
        terminate_count = 0
        both_terminations_entered = asyncio.Event()
        release_termination = asyncio.Event()

        class Stdin:
            def write(self, _data):
                pass

            async def drain(self):
                pass

        class Player:
            def __init__(self):
                self.stdin = Stdin()

        old_player = Player()

        class Worker:
            async def stream(self, _clause):
                yield type("Chunk", (), {"pcm": np.ones(20), "sample_rate": 24_000})()
                raise RuntimeError("worker failed")

            async def cancel(self, _generation):
                pass

        async def spawn(*_args, **_kwargs):
            return old_player

        async def gated_terminate(process):
            nonlocal terminate_count
            if process is old_player:
                terminate_count += 1
                if terminate_count == 2:
                    both_terminations_entered.set()
                await release_termination.wait()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(audio_module, "terminate", gated_terminate)
        speaker = Speaker(lambda *_args, **_kwargs: None, enabled=True)
        speaker.worker = Worker()
        speaker.sink = "verified-sink"

        stale_speak = asyncio.create_task(
            speaker.speak("hello", "old-turn", audible=True)
        )
        await asyncio.sleep(0)
        stale_stop = asyncio.create_task(speaker.stop())
        await asyncio.wait_for(both_terminations_entered.wait(), timeout=1)
        await speaker.stop()
        speaker.active = "new-session-warm"
        speaker.busy = True

        release_termination.set()
        results = await asyncio.gather(stale_speak, stale_stop, return_exceptions=True)

        assert isinstance(results[0], RuntimeError)
        assert speaker.active == "new-session-warm"
        assert speaker.busy is True

    asyncio.run(run())


def test_stale_warm_completion_cannot_clear_or_announce_a_new_owner():
    from alice.conversation.audio import Speaker

    async def run():
        warm_started = asyncio.Event()
        release_warm = asyncio.Event()
        events = []

        class Worker:
            async def stream(self, _clause):
                warm_started.set()
                await release_warm.wait()
                if False:
                    yield None

            async def cancel(self, _generation):
                pass

        speaker = Speaker(lambda stage, state, **details: events.append((stage, state)))
        speaker.worker = Worker()

        stale_warm = asyncio.create_task(speaker.warm())
        await asyncio.wait_for(warm_started.wait(), timeout=1)
        await speaker.stop()
        speaker.active = "new-session-warm"

        release_warm.set()
        await asyncio.wait_for(stale_warm, timeout=1)

        assert speaker.active == "new-session-warm"
        assert ("tts", "ready") not in events

    asyncio.run(run())
