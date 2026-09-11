"""Audio-active fault cancellation through the real stream and runtime."""

import asyncio
import threading
import time

import pytest
from test_face_stream import ROOT

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.hardware.face_scope import face_manifest
from alice.hardware.manifest import load_manifest
from alice.safety.supervisor import (
    OperatorApproval,
    PreflightEvidence,
    SafetySupervisor,
)
from alice.speech.face_stream import FaceCommandStream
from alice.speech.jaw_trial import trial_limits


def factory(cancel):
    from alice.speech.face_runtime import SimulatedFaceDriver

    full = load_manifest(ROOT / "hardware/alice-face-v1.yaml")
    scope = face_manifest(full)
    now = time.monotonic_ns()
    gate = SafetySupervisor(
        manifest=scope, limits=trial_limits(), clock=time.monotonic_ns
    )
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
            observed_monotonic_ns=now,
        )
    ).accepted
    assert gate.arm(
        OperatorApproval(approval_id="test", run_id="test", confirmed_monotonic_ns=now)
    ).accepted
    return FaceCommandStream(
        gate, SimulatedFaceDriver(scope), "mock", full, generation_id="g"
    )


def test_runtime_coalesces_then_homes_on_explicit_success():
    from alice.speech.face_runtime import FaceRuntime

    runtime = FaceRuntime(factory, on_fault=lambda: None)
    runtime.start()
    assert runtime.ready.wait(2)
    try:
        for i in range(50):
            runtime.offer(
                TargetUpdate(
                    offset_s=0,
                    targets=(
                        ActuatorTarget(
                            actuator_name="mouth_open", normalized_position=0.1
                        ),
                    ),
                ),
                "g",
                i,
            )
        time.sleep(0.06)
        runtime.complete()
        assert runtime.done.wait(3)
        runtime.raise_if_failed()
        assert runtime.stream.home_confirmed
        assert len(runtime.stream.consumed_revisions) < 50
        assert runtime.stream.driver.closed
    finally:
        runtime.abort("test cleanup")
        runtime.join()


@pytest.mark.parametrize(
    "fault", ["cancel", "stale", "controller", "partial", "watchdog"]
)
def test_runtime_fault_aborts_active_audio_ring(fault):
    import numpy as np

    from alice.speech.face_runtime import FaceRuntime
    from alice.speech.pcm_stream import PcmRingBuffer

    ring = PcmRingBuffer(24000)
    asyncio.run(ring.put(np.ones(2400, dtype=np.float32) * 0.05))
    runtime = FaceRuntime(factory, on_fault=ring.abort)
    runtime.start()
    assert runtime.ready.wait(2)
    entered, release = threading.Event(), threading.Event()
    try:
        runtime.raise_if_failed()
        if fault in ("controller", "partial", "watchdog"):
            original = runtime.stream.driver.stream_face_target

            def fail(token, request):
                entered.set()
                if fault == "watchdog":
                    release.wait(2)
                    return original(token, request)
                raise RuntimeError(f"injected {fault}")

            runtime.stream.driver.stream_face_target = fail
        runtime.offer(
            TargetUpdate(
                offset_s=0,
                targets=(
                    ActuatorTarget(actuator_name="mouth_open", normalized_position=0.1),
                ),
            ),
            "g",
            0,
        )
        if fault == "cancel":
            runtime.abort("cancelled audio")
        if fault == "watchdog":
            assert entered.wait(1)
            deadline = time.monotonic() + 1
            while ring.depth and time.monotonic() < deadline:
                time.sleep(0.005)
            assert ring.depth == 0
            release.set()
        assert runtime.done.wait(2)
        with pytest.raises(RuntimeError):
            runtime.raise_if_failed()
        assert ring.depth == 0
        assert runtime.stream.driver.closed
        assert not runtime.stream.home_confirmed
    finally:
        release.set()
        runtime.abort("test cleanup")
        runtime.join()


def test_success_holds_closed_mouth_frown_then_returns_home():
    from alice.speech.face_runtime import FaceRuntime

    runtime = FaceRuntime(factory, on_fault=lambda: None)
    runtime.start()
    assert runtime.ready.wait(2)
    pose = TargetUpdate(
        offset_s=0,
        targets=(
            ActuatorTarget(actuator_name="mouth_open", normalized_position=-1),
            ActuatorTarget(actuator_name="left_mouth_corner", normalized_position=-0.4),
            ActuatorTarget(actuator_name="right_mouth_corner", normalized_position=0.4),
        ),
    )
    try:
        runtime.complete(hold_pose=pose, hold_s=0.1)
        assert runtime.done.wait(5)
        runtime.raise_if_failed()
        records = runtime.stream.records
        closed = [
            r["status"]["sent_monotonic_ns"]
            for r in records
            if r.get("phase") == "post-speech"
            and "status" in r
            and r["request"]["targets"][0]["actuator_name"] == "mouth_open"
            and r["request"]["targets"][0]["normalized_position"] == -1
        ]
        assert closed and (max(closed) - min(closed)) / 1e9 >= 0.1
        assert runtime.stream.home_confirmed
        assert runtime.stream.states["left_mouth_corner"].position == 0
    finally:
        runtime.abort("test cleanup")
        runtime.join()


def test_cancel_during_closed_pose_stops_without_home_or_restoration():
    from alice.speech.face_runtime import FaceRuntime

    runtime = FaceRuntime(factory, on_fault=lambda: None)
    runtime.start()
    assert runtime.ready.wait(2)
    pose = TargetUpdate(
        offset_s=0,
        targets=(ActuatorTarget(actuator_name="mouth_open", normalized_position=-1),),
    )
    try:
        runtime.complete(hold_pose=pose, hold_s=1)
        deadline = time.monotonic() + 2
        while not runtime.stream.at_pose(pose) and time.monotonic() < deadline:
            time.sleep(0.005)
        assert runtime.stream.at_pose(pose)
        runtime.abort("cancel during hold")
        assert runtime.done.wait(1)
        with pytest.raises(RuntimeError, match="cancel during hold"):
            runtime.raise_if_failed()
        assert runtime.stream.driver.closed
        assert not runtime.stream.home_confirmed
        assert not any(r.get("phase") == "home" for r in runtime.stream.records)
    finally:
        runtime.abort("cleanup")
        runtime.join()


def test_post_speech_hold_waits_for_controller_pwm_arrival():
    from alice.speech.face_runtime import FaceRuntime

    def delayed_factory(cancel):
        stream = factory(cancel)
        original = stream.driver.stream_face_target
        closed_started = None

        def delayed_readback(token, request):
            nonlocal closed_started
            receipt = original(token, request)
            if (
                request.targets[0].actuator_name == "mouth_open"
                and receipt.target_qus == 4608
            ):
                if closed_started is None:
                    closed_started = time.monotonic()
                if time.monotonic() - closed_started < 0.3:
                    return receipt.model_copy(update={"observed_qus": 4800})
            return receipt

        stream.driver.stream_face_target = delayed_readback
        return stream

    runtime = FaceRuntime(delayed_factory, on_fault=lambda: None)
    runtime.start()
    assert runtime.ready.wait(2)
    pose = TargetUpdate(
        offset_s=0,
        targets=(ActuatorTarget(actuator_name="mouth_open", normalized_position=-1),),
    )
    try:
        runtime.complete(hold_pose=pose, hold_s=0.1)
        assert runtime.done.wait(3)
        runtime.raise_if_failed()
        closed = [
            r["status"]["sent_monotonic_ns"]
            for r in runtime.stream.records
            if r.get("phase") == "post-speech"
            and "status" in r
            and r["request"]["targets"][0]["actuator_name"] == "mouth_open"
            and r["status"]["target_qus"] == 4608
        ]
        assert (max(closed) - min(closed)) / 1e9 >= 0.4
    finally:
        runtime.abort("cleanup")
        runtime.join()
