"""Real supervisor tests for the bounded mouth-only trial path."""

import math

import pytest

from alice.speech.jaw_trial import JawTrialConfig, next_jaw_target


def test_jaw_start_reversal_and_return_respect_all_limits():
    config = JawTrialConfig()
    position = velocity = 0.0
    observed = []
    for desired in [-1.0] * 120 + [0.6] * 120 + [-1.0, 0.6] * 60 + [0.0] * 160:
        dt = 0.04
        target = next_jaw_target(position, velocity, desired, dt, config)
        next_velocity = (target - position) / dt
        assert -1 <= target <= 0.6
        assert abs(target - position) <= config.max_step + 1e-10
        assert abs(next_velocity) <= config.max_rate_per_s + 1e-10
        assert (
            abs(next_velocity - velocity) / dt <= config.max_acceleration_per_s2 + 1e-9
        )
        position, velocity = target, next_velocity
        observed.append(target)
    assert min(observed) < -0.99
    assert max(observed) > 0.59
    assert position == 0.0
    assert velocity == 0.0


@pytest.mark.parametrize("dt", [0, -0.1, 0.3, math.nan, math.inf])
def test_jaw_rejects_invalid_or_stalled_clock(dt):
    with pytest.raises(ValueError):
        next_jaw_target(0, 0, 0.6, dt, JawTrialConfig())


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_jaw_rejects_nonfinite_input(value):
    with pytest.raises(ValueError):
        next_jaw_target(0, 0, value, 0.04, JawTrialConfig())


def test_bounds_cannot_exceed_reviewed_trial_range():
    with pytest.raises(ValueError):
        JawTrialConfig(open_position=1.01)
    with pytest.raises(ValueError):
        JawTrialConfig(max_rate_per_s=20)


def running_stream(delay_s=0, initial_position=0.0, config=None):
    from pathlib import Path

    from alice.hardware.manifest import load_manifest
    from alice.hardware.mock_adapter import MockActuatorAdapter
    from alice.safety.supervisor import (
        OperatorApproval,
        PreflightEvidence,
        SafetySupervisor,
    )
    from alice.speech.jaw_trial import JawCommandStream, MouthOnlyAdapter, trial_limits

    config = config or JawTrialConfig()
    now = [1_000_000_000]

    def clock():
        return now[0]

    manifest = load_manifest(Path(__file__).parents[2] / "hardware/alice-face-v1.yaml")
    supervisor = SafetySupervisor(
        manifest=manifest,
        clock=clock,
        limits=trial_limits(config),
        allow_measured_start=True,
    )
    assert supervisor.preflight(
        PreflightEvidence(
            run_id="MOCK-jaw",
            hardware_id=manifest.hardware_id,
            calibration_sha256=manifest.calibration_sha256,
            controller_serial=manifest.controller.serial_number,
            requirement_results={
                r.requirement_id: True for r in manifest.preflight_requirements
            },
            competing_process_detected=False,
            controller_error_codes=(),
            home_verified=initial_position == 0,
            observed_targets=tuple(
                {
                    "actuator_name": a.name,
                    "normalized_position": initial_position
                    if a.name == "mouth_open"
                    else 0.0,
                }
                for a in manifest.actuators
            ),
            observed_monotonic_ns=now[0],
        )
    ).accepted
    assert supervisor.arm(
        OperatorApproval(
            approval_id="MOCK-ONLY", run_id="MOCK-jaw", confirmed_monotonic_ns=now[0]
        )
    ).accepted
    assert supervisor.start().accepted

    class DelayedMock(MockActuatorAdapter):
        def apply(self, authorization):
            now[0] += round(delay_s * 1e9)
            return super().apply(authorization)

    adapter = DelayedMock(
        manifest=manifest,
        clock=clock,
        permit_verifier=supervisor.actuation_permit_verifier,
    )
    boundary = MouthOnlyAdapter(adapter, config)
    stream = JawCommandStream(supervisor, boundary, config, clock=clock)
    return stream, now, supervisor, adapter, boundary


@pytest.mark.parametrize("delay_s", [0, 0.025, 0.09])
def test_jaw_waits_after_acknowledgement_and_commits_actual_timing(delay_s):
    stream, now, supervisor, adapter, _ = running_stream(delay_s)
    for _ in range(30):
        now[0] += 40_000_000
        before = now[0]
        status = stream.step(0.6, audio_sample=100)
        assert status is not None
        assert status.reported_monotonic_ns == before + round(delay_s * 1e9)
        assert stream.step(-1, audio_sample=120) is None
    assert len(stream.records) == 30
    assert set(adapter.positions) == {"mouth_open"}
    assert 0 < supervisor.committed_targets["mouth_open"] <= 0.6
    last, previous = stream.records[-1], stream.records[-2]
    delta = (
        last["request"]["targets"][0]["normalized_position"]
        - previous["request"]["targets"][0]["normalized_position"]
    )
    ack_interval = (
        last["status"]["reported_monotonic_ns"]
        - previous["status"]["reported_monotonic_ns"]
    ) / 1e9
    assert stream.velocity == pytest.approx(delta / ack_interval)


def test_final_adapter_rejects_extra_actuator_before_write():
    from alice.contracts.actuation import PoseRequest

    _, now, supervisor, adapter, boundary = running_stream()
    now[0] += 40_000_000
    request = PoseRequest(
        schema_version="pose-request/v1",
        request_id="wrong-axis",
        run_id="MOCK-jaw",
        hardware_id=supervisor.manifest.hardware_id,
        calibration_sha256=supervisor.manifest.calibration_sha256,
        issued_monotonic_ns=now[0],
        expires_monotonic_ns=now[0] + 100_000_000,
        targets=({"actuator_name": "neck_rotation", "normalized_position": 0.001},),
    )
    authorization = supervisor.authorize(request)
    assert authorization.authorized
    with pytest.raises(ValueError, match="mouth_open only"):
        boundary.apply(authorization)
    assert not adapter.positions


def test_target_remains_valid_when_authorization_clock_advances(monkeypatch):
    stream, now, supervisor, _, _ = running_stream(0.0004)
    authorize = supervisor.authorize

    def delayed_authorize(request):
        now[0] += 2_000_000
        return authorize(request)

    monkeypatch.setattr(supervisor, "authorize", delayed_authorize)
    for desired in [-1.0] * 100 + [0.6] * 100 + [0.0] * 100:
        now[0] += 40_000_000
        assert stream.step(desired, audio_sample=None) is not None


def test_operator_approved_full_open_endpoint_is_available():
    assert JawTrialConfig(open_position=1.0).open_position == 1.0


def test_fast_speech_profile_crosses_full_range_with_delayed_controller():
    config = JawTrialConfig(
        open_position=1,
        max_step=0.4,
        max_rate_per_s=10,
        max_acceleration_per_s2=200,
        response_time_s=0.03,
    )
    stream, now, supervisor, _, _ = running_stream(
        0.04, initial_position=-1, config=config
    )
    started = now[0]
    for _ in range(12):
        now[0] += 40_000_000
        assert stream.step(1.0, audio_sample=100) is not None
        if stream.position == 1.0 and abs(stream.velocity) < 1e-6:
            break
    assert stream.position == 1.0
    assert (now[0] - started) / 1e9 < 0.9
    assert all(
        -1 <= r["request"]["targets"][0]["normalized_position"] <= 1
        for r in stream.records
    )
    assert supervisor.state.value == "running"
