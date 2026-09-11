"""Exercise the trusted executor through the real encoder and a byte transport."""

from pathlib import Path

import pytest

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.hardware.manifest import load_manifest
from alice.safety.supervisor import (
    OperatorApproval,
    PreflightEvidence,
    SafetySupervisor,
)
from alice.speech.jaw_trial import trial_limits

ROOT = Path(__file__).resolve().parents[2]


class Wire:
    def __init__(self, now):
        self.now = now
        self.positions = {3: 5626, 4: 6173, 5: 5918, 6: 5059, 9: 6499, 11: 5524}
        self.writes = []
        self.response = b""
        self.closed = False
        self.errors = 0
        self.partial = False
        self.delay_ns = 0
        self.after_target = None

    def write(self, payload):
        assert not self.closed
        self.writes.append((self.now[0], payload))
        if self.partial:
            return 1
        if payload[0] == 0x84:
            self.positions[payload[1]] = payload[2] + 128 * payload[3]
            if self.after_target:
                self.after_target()
        elif payload[0] == 0x90:
            self.response = self.positions[payload[1]].to_bytes(2, "little")
        elif payload[0] == 0xA1:
            self.response = self.errors.to_bytes(2, "little")
        return len(payload)

    def read(self, count):
        self.now[0] += self.delay_ns
        result, self.response = self.response[:count], self.response[count:]
        return result

    def close(self):
        self.closed = True


def setup(tmp_path):
    from alice.hardware.face_scope import face_manifest, face_profiles
    from alice.hardware.maestro_face import MaestroFaceAdapter
    from alice.speech.face_stream import FaceCommandStream

    full = load_manifest(ROOT / "hardware/alice-face-v1.yaml")
    scope = face_manifest(full)
    now = [1_000_000_000]

    def clock():
        return now[0]

    gate = SafetySupervisor(manifest=scope, clock=clock, limits=trial_limits())
    assert gate.preflight(
        PreflightEvidence(
            run_id="test",
            hardware_id=scope.hardware_id,
            calibration_sha256=scope.calibration_sha256,
            controller_serial=scope.controller.serial_number,
            requirement_results={
                r.requirement_id: True for r in scope.preflight_requirements
            },
            competing_process_detected=False,
            controller_error_codes=(),
            home_verified=True,
            observed_monotonic_ns=now[0],
        )
    ).accepted
    assert gate.arm(
        OperatorApproval(
            approval_id="mock",
            run_id="test",
            confirmed_monotonic_ns=now[0],
        )
    ).accepted
    wire = Wire(now)
    driver = MaestroFaceAdapter(
        manifest=scope,
        stable_device_path=full.controller.command_device_path,
        expected_controller_serial="00037376",
        required_enable_token="test",
        clock=clock,
        permit_verifier=gate.actuation_permit_verifier,
        transport_factory=lambda *_: wire,
        sleeper=lambda s: now.__setitem__(0, now[0] + round(s * 1e9)),
    )
    driver.open("test")
    stream = FaceCommandStream(
        gate,
        driver,
        "test",
        full,
        generation_id="g",
        clock=clock,
        sleeper=lambda s: now.__setitem__(0, now[0] + round(s * 1e9)),
    )
    return stream, driver, wire, now, face_profiles()


def proposal(**positions):
    return TargetUpdate(
        offset_s=0,
        targets=tuple(
            ActuatorTarget(actuator_name=k, normalized_position=v)
            for k, v in positions.items()
        ),
    )


def test_only_selected_channels_emit_and_sparse_updates_keep_coupled_pose(tmp_path):
    stream, _, wire, now, _ = setup(tmp_path)
    stream.offer(
        proposal(
            mouth_open=1,
            lower_eyelids=-0.4,
            upper_eyelids=-0.4,
            left_mouth_corner=0.2,
            right_mouth_corner=-0.2,
            neck_rotation=1,
            right_eye_horizontal=1,
        ),
        "g",
        0,
    )
    for i in range(30):
        now[0] += 40_000_000
        stream.offer(proposal(mouth_open=0.8), "g", i)
        stream.step()
    assert {p[1] for _, p in wire.writes if p[0] == 0x84} == {3, 4, 5, 6, 9, 11}
    assert wire.positions[3] < 5626 and wire.positions[4] < 6173
    assert wire.positions[9] > 6499 and wire.positions[11] < 5524
    assert stream.latest_positions["lower_eyelids"] == -0.4


def test_each_channel_uses_its_own_write_clock_and_quantized_limits(tmp_path):
    stream, _, wire, now, profiles = setup(tmp_path)
    previous = {name: (now[0], 0.0, 0.0) for name in profiles}
    for i in range(80):
        now[0] += 40_000_000
        wire.delay_ns = (i % 4) * 1_000_000
        stream.offer(
            proposal(**{n: (1 if i % 20 < 10 else -1) for n in profiles}), "g", i
        )
        stream.step()
        for name, state in stream.states.items():
            t, p, v = previous[name]
            if state.sent_ns == t:
                continue
            dt = (state.sent_ns - t) / 1e9
            velocity = (state.position - p) / dt
            c = profiles[name]
            assert abs(state.position - p) <= c.max_step + 1e-9
            assert abs(velocity) <= c.max_rate_per_s + 1e-9
            assert abs(velocity - v) / dt <= c.max_acceleration_per_s2 + 1e-9
            assert c.closed_position <= state.position <= c.open_position
            previous[name] = (state.sent_ns, state.position, velocity)


@pytest.mark.parametrize(
    "fault", ["stale", "generation", "cancel", "controller", "partial"]
)
def test_fault_is_terminal_and_closes_without_restoration_bytes(tmp_path, fault):
    stream, driver, wire, now, _ = setup(tmp_path)
    driver.enable_fast_jaw_response("test")
    stream.offer(proposal(mouth_open=0.5), "g", 0)
    now[0] += 40_000_000
    if fault == "stale":
        now[0] += 300_000_000
    elif fault == "controller":
        wire.errors = 1
    elif fault == "partial":
        wire.partial = True
    elif fault == "cancel":
        stream.revoke("cancelled")
    mark = len(wire.writes)
    with pytest.raises((RuntimeError, ValueError)):
        if fault == "generation":
            stream.offer(proposal(mouth_open=1), "old", 1)
        else:
            stream.step()
    after = len(wire.writes)
    assert wire.closed
    assert not any(p[0] in (0x87, 0x89) for _, p in wire.writes[mark:])
    with pytest.raises((RuntimeError, ValueError)):
        stream.step()
    assert len(wire.writes) == after


def test_cancellation_inside_transaction_stops_before_readback(tmp_path):
    stream, _, wire, now, _ = setup(tmp_path)
    stream.offer(proposal(mouth_open=0.5), "g", 0)
    now[0] += 40_000_000
    wire.after_target = lambda: stream.revoke("cancel during serial write")
    with pytest.raises(RuntimeError):
        stream.step()
    assert len(wire.writes) == 1
    assert wire.closed


def test_finish_confirms_every_home_before_restoring_only_jaw(tmp_path):
    stream, driver, wire, _, _ = setup(tmp_path)
    driver.enable_fast_jaw_response("test")
    wire.writes.clear()
    stream.finish()
    first_profile = next(i for i, (_, p) in enumerate(wire.writes) if p[0] == 0x87)
    assert {p[1] for _, p in wire.writes[:first_profile] if p[0] == 0x90} == {
        3,
        4,
        5,
        6,
        9,
        11,
    }
    assert [p for _, p in wire.writes if p[0] in (0x87, 0x89)] == [
        b"\x87\x06\x00\x00",
        b"\x89\x06\x0b\x00",
    ]
    assert driver.jaw_response_override["status"] == "restored"


def test_off_home_completion_cannot_restore(tmp_path):
    stream, driver, wire, _, _ = setup(tmp_path)
    driver.enable_fast_jaw_response("test")
    wire.positions[9] += 2
    wire.writes.clear()
    with pytest.raises(RuntimeError, match="Home"):
        stream.finish()
    assert wire.closed
    assert not any(p[0] in (0x87, 0x89) for _, p in wire.writes)


def test_expression_motion_returns_to_exact_home_after_quantization(tmp_path):
    stream, driver, wire, now, profiles = setup(tmp_path)
    driver.enable_fast_jaw_response("test")
    for i in range(50):
        now[0] += 40_000_000
        stream.offer(proposal(**{name: -0.25 for name in profiles}), "g", i)
        stream.step()
    assert any(s.position != 0 for s in stream.states.values())
    for _ in range(150):
        now[0] += 40_000_000
        stream.step(home=True)
        if stream.at_home:
            break
    assert stream.at_home, {n: s.position for n, s in stream.states.items()}
    stream.finish()
    assert stream.home_confirmed
    assert driver.jaw_response_override["status"] == "restored"


@pytest.mark.parametrize("partial", [False, True])
def test_fault_retains_completed_or_uncertain_write_evidence(tmp_path, partial):
    stream, _, wire, now, _ = setup(tmp_path)
    stream.offer(proposal(mouth_open=0.5), "g", 0)
    now[0] += 40_000_000
    wire.errors, wire.partial = 1, partial
    with pytest.raises(RuntimeError):
        stream.step()
    tx = stream.records[-1]["transaction"]
    assert tx["target_qus"] == 5178
    assert tx["write_started_monotonic_ns"] == now[0]
    assert tx["write_completed"] is (not partial)
    assert tx["observed_qus"] is None
    assert tx["error"]


def test_cancelled_startup_opens_no_transport(tmp_path):
    from threading import Event

    stream, driver, wire, _, _ = setup(tmp_path)
    driver.close()
    cancelled = Event()
    cancelled.set()
    with pytest.raises(RuntimeError):
        driver.open("test", cancel=cancelled)
    assert wire.closed and not wire.writes


def test_inference_does_not_refresh_stale_dac_frame(tmp_path):
    stream, _, wire, now, _ = setup(tmp_path)
    captured = now[0]
    now[0] += 300_000_000
    with pytest.raises(ValueError, match="stale"):
        stream.offer(proposal(mouth_open=1), "g", 0, source_monotonic_ns=captured)
    assert wire.closed and not wire.writes
