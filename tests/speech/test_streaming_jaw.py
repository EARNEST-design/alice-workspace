import pytest
from test_jaw_trial import running_stream

from alice.hardware.maestro_adapter import (
    MaestroPreflightSnapshot,
    MaestroStreamReceipt,
)
from alice.speech.jaw_trial import JawTrialConfig


def make_stream():
    from alice.experiments.jaw_trial_cli import scoped_manifest
    from alice.safety.supervisor import (
        OperatorApproval,
        PreflightEvidence,
        SafetySupervisor,
    )
    from alice.speech.jaw_trial import trial_limits
    from alice.speech.streaming_jaw import StreamingJawCommandStream

    _, now, base, _, _ = running_stream()
    manifest = scoped_manifest(base.manifest)
    config = JawTrialConfig(
        open_position=1,
        max_step=0.4,
        max_rate_per_s=10,
        max_acceleration_per_s2=200,
        response_time_s=0.03,
    )

    def clock():
        return now[0]

    gate = SafetySupervisor(manifest=manifest, limits=trial_limits(config), clock=clock)
    assert gate.preflight(
        PreflightEvidence(
            run_id="stream-test",
            hardware_id=manifest.hardware_id,
            calibration_sha256=manifest.calibration_sha256,
            controller_serial=manifest.controller.serial_number,
            requirement_results={
                r.requirement_id: True for r in manifest.preflight_requirements
            },
            competing_process_detected=False,
            controller_error_codes=(),
            home_verified=True,
            observed_monotonic_ns=now[0],
        )
    ).accepted
    assert gate.arm(
        OperatorApproval(
            approval_id="test", run_id="stream-test", confirmed_monotonic_ns=now[0]
        )
    ).accepted

    class Driver:
        def __init__(self):
            self.sent = []
            self.closed = False
            self.observed = 5059
            self.restored = False

        def stream_jaw_target(self, token, request):
            self.sent.append(request)
            return MaestroStreamReceipt(
                target_qus=manifest.actuator("mouth_open").target_qus(
                    request.targets[0].normalized_position
                ),
                observed_qus=self.observed,
                sent_monotonic_ns=now[0],
                reported_monotonic_ns=now[0],
            )

        def read_only_preflight(self, names):
            return MaestroPreflightSnapshot(
                controller_error_register=0,
                positions_qus={"mouth_open": self.observed},
                observed_monotonic_ns=now[0],
            )

        def restore_jaw_response(self):
            assert self.observed == 5059
            self.restored = True

        def close(self):
            self.closed = True

    driver = Driver()
    stream = StreamingJawCommandStream(
        gate,
        driver,
        "test-token",
        config,
        clock=clock,
        sleeper=lambda s: now.__setitem__(0, now[0] + round(s * 1e9)),
    )
    return stream, now, driver


def test_stream_can_continue_while_controller_is_still_moving():
    stream, now, driver = make_stream()
    for _ in range(15):
        now[0] += 40_000_000
        receipt = stream.step(1.0, audio_sample=123)
        assert receipt.state == "sent"
        assert receipt.observed_qus == 5059
    assert stream.position == 1.0
    assert len(driver.sent) == 15
    assert all(t.actuator_name == "mouth_open" for r in driver.sent for t in r.targets)
    assert all(r["status"]["state"] == "sent" for r in stream.records)


def test_stream_gap_revokes_before_another_write():
    stream, now, driver = make_stream()
    now[0] += 300_000_000
    with pytest.raises(ValueError, match="stalled"):
        stream.step(1.0, audio_sample=0)
    assert not driver.sent
    with pytest.raises(RuntimeError, match="inactive"):
        stream.step(0.0, audio_sample=0)


def test_stream_finish_requires_observed_home_and_zero_command_velocity():
    stream, now, driver = make_stream()
    driver.observed = 5440
    with pytest.raises(RuntimeError, match="Home"):
        stream.finish()
    assert driver.closed


def test_streaming_playback_completes_without_faking_applied_receipts():
    import time

    from test_jaw_playback import short_speech

    from alice.speech.jaw_playback import run_jaw_playback

    stream, now, driver = make_stream()

    def sleep(seconds):
        now[0] += round(seconds * 1e9)
        time.sleep(0.00005)

    def player(prepared, emit, *, cancel):
        for frame in prepared.frames:
            emit(frame)
            time.sleep(0.003)
        return "completed"

    result = run_jaw_playback(short_speech(), stream, player=player, sleeper=sleep)
    assert result["audio_outcome"] == "completed"
    assert result["controller_home_confirmed"]
    assert driver.restored
    assert driver.closed
    assert any(r.get("audio_sample") is not None for r in stream.records)
    assert all(r["status"]["state"] == "sent" for r in stream.records if "status" in r)
    assert any("completion_observation" in r for r in stream.records)


def test_stream_rejects_receipt_clock_stall_and_closes(monkeypatch):
    stream, now, driver = make_stream()
    send = driver.stream_jaw_target

    def stalled(token, request):
        now[0] += 300_000_000
        return send(token, request)

    monkeypatch.setattr(driver, "stream_jaw_target", stalled)
    now[0] += 40_000_000
    with pytest.raises(RuntimeError, match="stalled"):
        stream.step(1, audio_sample=0)
    assert driver.closed


@pytest.mark.parametrize("slow", [False, True])
def test_variable_readback_latency_preserves_sent_target_acceleration(
    monkeypatch, slow
):
    import random

    stream, now, driver = make_stream()
    if slow:
        stream.config = JawTrialConfig(open_position=1)
    send = driver.stream_jaw_target
    writes = [(now[0], stream.position)]
    rng = random.Random(42)

    def delayed(token, request, **kwargs):
        writes.append((now[0], request.targets[0].normalized_position))
        receipt = send(token, request, **kwargs)
        now[0] += rng.choice([0, 1, 20, 45]) * 1_000_000
        return receipt.model_copy(update={"reported_monotonic_ns": now[0]})

    monkeypatch.setattr(driver, "stream_jaw_target", delayed)
    velocity = 0.0
    for i in range(100):
        now[0] += 40_000_000
        stream.step(1.0 if i % 20 < 10 else -1.0, audio_sample=i)
        (t0, q0), (t1, q1) = writes[-2:]
        elapsed = (t1 - t0) / 1e9
        next_velocity = (q1 - q0) / elapsed
        assert (
            abs(next_velocity - velocity) / elapsed
            <= stream.config.max_acceleration_per_s2 + 1e-9
        )
        assert abs(next_velocity) <= stream.config.max_rate_per_s + 1e-9
        velocity = next_velocity


@pytest.mark.parametrize("at_finish", [False, True])
def test_stream_cleanup_keeps_primary_fault(monkeypatch, at_finish):
    stream, now, driver = make_stream()

    def bad_close():
        raise OSError("injected close failure")

    monkeypatch.setattr(driver, "close", bad_close)
    if at_finish:
        driver.observed = 5440
        with pytest.raises(RuntimeError, match="Home") as failure:
            stream.finish()
    else:
        now[0] += 300_000_000
        with pytest.raises(ValueError, match="stalled") as failure:
            stream.step(1, audio_sample=0)
    assert any("injected close failure" in note for note in failure.value.__notes__)
    assert "injected close failure" in str(stream.records[-1])
