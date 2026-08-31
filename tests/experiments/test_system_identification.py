from __future__ import annotations

import hashlib
import inspect
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from alice.analysis.system_identification import analyze_identification_artifacts
from alice.contracts.actuation import ActuatorStatus, ActuatorStatusState
from alice.contracts.blendshapes import (
    BlendshapeObservation,
    BlendshapeScore,
    ObservationValidity,
)
from alice.experiments import system_identification
from alice.experiments.manifest import IdentificationObserverProvenance, RunStatus
from alice.experiments.system_identification import (
    IdentificationConfig,
    IdentificationStep,
    run_mock_identification,
)
from alice.hardware.adapter import AdapterIdentity, AdapterMode
from alice.hardware.manifest import HardwareManifest, load_manifest
from alice.hardware.mock_adapter import MockActuatorAdapter
from alice.safety.supervisor import (
    OperatorApproval,
    PreflightEvidence,
    RunState,
    SafetyLimits,
    SafetySupervisor,
)


class FakeClock:
    def __init__(self) -> None:
        self.now_ns = 1_000_000_000

    def __call__(self) -> int:
        return self.now_ns

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0.0
        self.now_ns += round(seconds * 1_000_000_000)


class RecordingObserver:
    def __init__(
        self,
        clock: FakeClock,
        *,
        fail_at_call: int | None = None,
        values: list[float] | None = None,
    ) -> None:
        self.clock = clock
        self.fail_at_call = fail_at_call
        self.values = values or [0.2]
        self.calls: list[tuple[str, str, int]] = []

    def observe(self, *, run_id: str, step_id: str) -> BlendshapeObservation:
        call_number = len(self.calls) + 1
        self.calls.append((run_id, step_id, self.clock()))
        if call_number == self.fail_at_call:
            return observation(
                run_id=run_id,
                monotonic_ns=self.clock(),
                validity=ObservationValidity.NO_FACE,
            )
        value = self.values[(call_number - 1) % len(self.values)]
        return observation(run_id=run_id, monotonic_ns=self.clock(), value=value)

    @property
    def provenance(self) -> IdentificationObserverProvenance:
        return IdentificationObserverProvenance.model_validate(
            {
                "camera_id": "mock-camera",
                "detector": "mock-detector",
                "detector_model_sha256": "a" * 64,
                "camera_settings": {
                    name: {
                        "availability": "available",
                        "value": value,
                        "set_succeeded": True,
                    }
                    for name, value in {
                        "width": 640,
                        "height": 480,
                        "fps": 30,
                        "focus": 0,
                        "exposure": -5,
                    }.items()
                },
            }
        )


class RaisingObserver(RecordingObserver):
    def observe(self, *, run_id: str, step_id: str) -> BlendshapeObservation:
        if len(self.calls) == 3:
            raise RuntimeError("raw camera details must not enter artifacts")
        return super().observe(run_id=run_id, step_id=step_id)


class RecordingAdapter(MockActuatorAdapter):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.applied_wrappers: list[object] = []

    def apply(self, authorization: Any) -> ActuatorStatus:
        self.applied_wrappers.append(authorization)
        return super().apply(authorization)


class FaultingAdapter(RecordingAdapter):
    def __init__(self, *, fault_on_call: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fault_on_call = fault_on_call

    def apply(self, authorization: Any) -> ActuatorStatus:
        status = super().apply(authorization)
        if len(self.applied_wrappers) != self.fault_on_call:
            return status
        return status.model_copy(
            update={
                "state": ActuatorStatusState.FAULT,
                "fault_code": "mock-controller-fault",
                "detail": "injected deterministic fault",
            }
        )


class UnknownApplicationAdapter(RecordingAdapter):
    def apply(self, authorization: Any) -> ActuatorStatus:
        if len(self.applied_wrappers) == 1:
            self.applied_wrappers.append(authorization)
            raise TimeoutError("application state unknown")
        return super().apply(authorization)


class LyingSettlingAdapter(RecordingAdapter):
    def apply(self, authorization: Any) -> ActuatorStatus:
        status = super().apply(authorization)
        sample = status.controller_output_samples[0]
        return status.model_copy(
            update={
                "controller_output_samples": (
                    sample.model_copy(update={"observed_qus": sample.target_qus + 1}),
                ),
                "targets_reached": True,
            }
        )


class HardwareCapableFake(RecordingAdapter):
    @property
    def identity(self) -> AdapterIdentity:
        return AdapterIdentity(
            backend="maestro",
            mode=AdapterMode.HARDWARE,
            hardware_capable=True,
        )


class StepObserver(RecordingObserver):
    def __init__(self, clock: FakeClock, by_step: dict[str, float]) -> None:
        super().__init__(clock)
        self.by_step = by_step

    def observe(self, *, run_id: str, step_id: str) -> BlendshapeObservation:
        self.calls.append((run_id, step_id, self.clock()))
        return observation(
            run_id=run_id,
            monotonic_ns=self.clock(),
            value=self.by_step[step_id],
        )


class SlowObserver(RecordingObserver):
    def observe(self, *, run_id: str, step_id: str) -> BlendshapeObservation:
        self.clock.sleep(6.0)
        return super().observe(run_id=run_id, step_id=step_id)


class FailingOnceSleeper:
    def __init__(self, clock: FakeClock, fail_on_call: int) -> None:
        self.clock = clock
        self.fail_on_call = fail_on_call
        self.calls = 0

    def __call__(self, seconds: float) -> None:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("private sleeper detail")
        self.clock.sleep(seconds)


class WrongIdentityObserver(RecordingObserver):
    @property
    def provenance(self) -> IdentificationObserverProvenance:
        return super().provenance.model_copy(update={"camera_id": "other-camera"})


class RaisingProvenanceObserver(RecordingObserver):
    @property
    def provenance(self) -> IdentificationObserverProvenance:
        raise RuntimeError("secret camera path and participant detail")


@pytest.fixture
def manifest() -> HardwareManifest:
    return load_manifest(Path("hardware/alice-face-v1.yaml"))


def observation(
    *,
    run_id: str,
    monotonic_ns: int,
    value: float = 0.2,
    validity: ObservationValidity = ObservationValidity.VALID,
) -> BlendshapeObservation:
    captured = datetime(2026, 8, 31, tzinfo=UTC) + timedelta(
        microseconds=monotonic_ns // 1_000
    )
    valid = validity is ObservationValidity.VALID
    return BlendshapeObservation(
        schema_version="blendshape-observation/v1",
        captured_at=captured,
        observed_at=captured,
        monotonic_ns=monotonic_ns,
        camera_id="mock-camera",
        run_id=run_id,
        detector="mock-detector",
        detector_model_sha256="a" * 64,
        image_width=640,
        image_height=480,
        face_confidence=1.0 if valid else None,
        validity=validity,
        invalid_reason=None if valid else "no face",
        scores=(BlendshapeScore(name="jawOpen", score=value),) if valid else (),
    )


def config(manifest: HardwareManifest, **updates: Any) -> IdentificationConfig:
    manifest_path = Path("hardware/alice-face-v1.yaml")
    values: dict[str, Any] = {
        "schema_version": "identification-config/v1",
        "run_id": "mock-identification-001",
        "adapter": "mock",
        "hardware_id": manifest.hardware_id,
        "calibration_sha256": manifest.calibration_sha256,
        "hardware_manifest_path": str(manifest_path),
        "hardware_manifest_sha256": __import__("hashlib").sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
        "hardware_manifest_canonical_sha256": manifest.canonical_sha256,
        "actuator_names": ("mouth_open",),
        "offsets": (0.1, -0.1),
        "samples_per_step": 3,
        "command_interval_ms": 250,
        "controller_settle_ms": 20,
        "visual_settle_ms": 30,
        "sample_interval_ms": 10,
        "step_timeout_ms": 2_000,
        "command_ttl_ms": 100,
        "maximum_visual_variance": 0.01,
        "home_delta_tolerances": {"jawOpen": 0.01},
        "recovery_timeout_ms": 2_000,
        "maximum_recovery_attempts": 20,
        "random_seeds": (),
        "provenance": {
            "kind": "deterministic-mock",
            "source": "tests/experiments/test_system_identification.py",
        },
        "retention": "derived_observations_only",
        "observer": {
            "camera_id": "mock-camera",
            "detector": "mock-detector",
            "detector_model_sha256": "a" * 64,
            "camera_settings": {
                name: {
                    "availability": "available",
                    "value": value,
                    "set_succeeded": True,
                }
                for name, value in {
                    "width": 640,
                    "height": 480,
                    "fps": 30,
                    "focus": 0,
                    "exposure": -5,
                }.items()
            },
        },
    }
    values.update(updates)
    return IdentificationConfig.model_validate(values)


def armed_supervisor(
    manifest: HardwareManifest, clock: FakeClock
) -> SafetySupervisor:
    supervisor = SafetySupervisor(
        manifest=manifest,
        limits=SafetyLimits(
            max_step=0.2,
            max_rate_per_second=1.0,
            max_acceleration_per_second_squared=10.0,
            watchdog_timeout_ns=5_000_000_000,
            approval_max_age_ns=60_000_000_000,
            preflight_max_age_ns=60_000_000_000,
            command_max_age_ns=100_000_000,
            recovery_command_ttl_ns=1_000_000_000,
        ),
        clock=clock,
    )
    evidence = PreflightEvidence(
        run_id="mock-identification-001",
        hardware_id=manifest.hardware_id,
        calibration_sha256=manifest.calibration_sha256,
        controller_serial=manifest.controller.serial_number,
        requirement_results={
            requirement.requirement_id: True
            for requirement in manifest.preflight_requirements
        },
        competing_process_detected=False,
        controller_error_codes=(),
        home_verified=True,
        observed_monotonic_ns=clock(),
    )
    assert supervisor.preflight(evidence).accepted
    assert supervisor.arm(
        OperatorApproval(
            approval_id="mock-approval",
            run_id="mock-identification-001",
            confirmed_monotonic_ns=clock(),
        )
    ).accepted
    return supervisor


def adapter(
    manifest: HardwareManifest,
    supervisor: SafetySupervisor,
    clock: FakeClock,
) -> RecordingAdapter:
    return RecordingAdapter(
        manifest=manifest,
        clock=clock,
        permit_verifier=supervisor.actuation_permit_verifier,
    )


def jsonl(run_dir: Path, name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (run_dir / name).read_text().splitlines()]


def test_identification_step_is_typed_and_mock_config_rejects_maestro(
    manifest: HardwareManifest,
) -> None:
    step = IdentificationStep(
        step_id="step-001",
        actuator_name="mouth_open",
        normalized_position=0.1,
        phase="positive",
    )
    assert step.normalized_position == 0.1
    with pytest.raises(ValueError, match="mock"):
        config(manifest, adapter="maestro")


def test_packaged_mock_config_covers_manifest_and_binds_calibration(
    manifest: HardwareManifest,
) -> None:
    document = yaml.safe_load(
        Path("config/experiments/actuator-identification-mock.yaml").read_text()
    )
    loaded = IdentificationConfig.model_validate(document)

    assert loaded.hardware_id == manifest.hardware_id
    assert loaded.calibration_sha256 == manifest.calibration_sha256
    assert loaded.actuator_names == tuple(item.name for item in manifest.actuators)


def test_exact_sequence_waits_for_status_and_settling_and_correlates_artifacts(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    observer = RecordingObserver(clock)
    run_dir = tmp_path / "run"

    result = run_mock_identification(
        config(manifest), observer, supervisor, run_dir, clock, clock.sleep
    )

    assert result.status is RunStatus.COMPLETED
    commands = jsonl(run_dir, "commands.jsonl")
    assert [record["normalized_position"] for record in commands] == [
        0.0,
        0.1,
        0.0,
        -0.1,
        0.0,
    ]
    assert [record["phase"] for record in commands] == [
        "home",
        "positive",
        "home",
        "negative",
        "home",
    ]
    statuses = jsonl(run_dir, "statuses.jsonl")
    observations = jsonl(run_dir, "observations.jsonl")
    assert {item["step_id"] for item in commands} == {
        item["step_id"] for item in statuses
    } == {item["step_id"] for item in observations}
    status_time = {item["step_id"]: item["monotonic_ns"] for item in statuses}
    for item in observations:
        assert item["observation"]["monotonic_ns"] >= (
            status_time[item["step_id"]] + 50_000_000
        )
    assert len(observer.calls) == 5 * 3
    assert all(item["authorization_kind"] == "normal" for item in commands)
    assert supervisor.state is RunState.DISARMED
    assert supervisor.committed_targets["mouth_open"] == 0.0
    assert result.artifacts["commands.jsonl"].sha256
    assert result.artifacts["statuses.jsonl"].sha256
    assert result.artifacts["transitions.jsonl"].sha256
    assert result.artifacts["faults.jsonl"].sha256
    assert (
        json.loads((run_dir / "metrics.json").read_text())["state"]
        == "pending_analysis"
    )
    assert "pending" in (run_dir / "conclusion.md").read_text().lower()
    controller = jsonl(run_dir, "controller-settling.jsonl")
    assert controller
    assert all(item["decision"]["target_reached"] for item in controller)
    assert all(item["decision"]["samples"] for item in controller)
    visual = jsonl(run_dir, "visual-settling.jsonl")
    assert len(visual) == 5
    assert visual[0]["decision"]["established_home_baseline"] is True
    assert visual[-1]["decision"]["home_verified"] is True
    stored_manifest = json.loads((run_dir / "manifest.json").read_text())
    assert stored_manifest["run_kind"] == "actuator_identification"
    metadata = stored_manifest["identification_metadata"]
    assert metadata["adapter_identity"]["backend"] == "mock"
    assert metadata["observer"]["camera_id"] == "mock-camera"
    assert metadata["safety_limits"]["watchdog_timeout_ns"] == 5_000_000_000
    assert metadata["preflight"]["run_id"] == config(manifest).run_id
    assert metadata["approval"]["approval_id"] == "mock-approval"
    assert metadata["hardware_manifest_sha256"] == config(
        manifest
    ).hardware_manifest_sha256
    assert stored_manifest["camera_settings"]["width"]["value"] == 640


def test_private_core_records_raw_config_and_checksummed_hardware_provenance(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    observer = RecordingObserver(clock)
    adapter = MockActuatorAdapter(
        manifest=manifest,
        clock=clock,
        permit_verifier=supervisor.actuation_permit_verifier,
    )
    raw_config_sha256 = "e" * 64
    execution_config_sha256 = hashlib.sha256(
        json.dumps(
            config(manifest).model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    provenance = system_identification.HardwareIdentificationProvenance.model_validate(
        {
            "approved_raw_config_sha256": raw_config_sha256,
            "execution_config_sha256": execution_config_sha256,
            "hardware_approval": {
                "approval_id": "approval",
                "run_id": "mock-identification-001",
                "config_sha256": raw_config_sha256,
                "manifest_sha256": "f" * 64,
                "electrical_evidence_sha256": "d" * 64,
                "approved_at": "2026-08-31T00:00:00Z",
                "approved_monotonic_ns": 1,
                "source": "operator",
                "operator_acknowledgment": "reviewed",
            },
            "power_challenge": {
                "challenge_id": "challenge",
                "run_id": "mock-identification-001",
                "config_sha256": raw_config_sha256,
                "manifest_sha256": "f" * 64,
                "electrical_evidence_sha256": "d" * 64,
                "issued_at": "2026-08-31T00:00:01Z",
                "issued_monotonic_ns": 2,
                "expires_monotonic_ns": 100,
                "challenge_sha256": "c" * 64,
            },
            "power_confirmation": {
                "run_id": "mock-identification-001",
                "challenge_id": "challenge",
                "config_sha256": raw_config_sha256,
                "manifest_sha256": "f" * 64,
                "electrical_evidence_sha256": "d" * 64,
                "challenge_sha256": "c" * 64,
                "confirmed_at": "2026-08-31T00:00:02Z",
                "confirmed_monotonic_ns": 3,
                "source": "operator",
                "operator_acknowledgment": "power enabled",
            },
            "electrical_safety": {
                "evidence_id": "electrical",
                "evidence_sha256": "d" * 64,
                "source": "review.pdf",
                "source_document_sha256": "b" * 64,
                "reviewed_at": "2026-08-31T00:00:00Z",
                "reviewer": "reviewer",
                "supply_voltage_v": 6.0,
                "current_limit_a": 5.0,
                "scope": "test fixture",
            },
            "usb_identity": {
                "serial_number": "00037376",
                "interface_number": "00",
                "resolved_tty": "/dev/ttyACM0",
                "stable_device_path": "/dev/serial/by-id/controller-if00",
            },
            "read_only_preflight": {
                "controller_error_register": 0,
                "positions": [
                    {
                        "actuator_name": "mouth_open",
                        "observed_qus": 5059,
                        "expected_home_qus": 5059,
                        "tolerance_qus": 12,
                    }
                ],
                "observed_monotonic_ns": 4,
                "issued_set_target": False,
            },
            "independent_watchdog": {
                "implementation": "process-local-os-monotonic-thread/v1",
                "clock": "time.monotonic",
                "timeout_ms": 2500,
                "actions": ["revoke-permits", "close-adapter"],
                "survives_process_death": False,
            },
        }
    )
    run_dir = tmp_path / "hardware-provenance-run"

    result = system_identification._run_identification_core(
        config=config(manifest),
        observer=observer,
        supervisor=supervisor,
        adapter=adapter,
        output_dir=run_dir,
        clock=clock,
        sleeper=clock.sleep,
        hardware_provenance=provenance,
    )

    assert result.identification_metadata is not None
    assert result.identification_metadata.config_sha256 == execution_config_sha256
    assert result.identification_metadata.hardware_provenance == provenance
    record = result.artifacts["hardware-provenance.json"]
    assert record.sha256 == hashlib.sha256(
        (run_dir / "hardware-provenance.json").read_bytes()
    ).hexdigest()
    metrics = analyze_identification_artifacts([run_dir])
    assert metrics.session_ids == ("mock-identification-001",)


@pytest.mark.parametrize("failure", ["camera", "variance", "timeout"])
def test_observation_failures_abort_before_next_normal_movement_and_recover_home(
    tmp_path: Path, manifest: HardwareManifest, failure: str
) -> None:
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    observer = RecordingObserver(
        clock,
        fail_at_call=4 if failure == "camera" else None,
        values=[0.0, 1.0, 0.0] if failure == "variance" else None,
    )
    cfg = config(
        manifest,
        step_timeout_ms=40 if failure == "timeout" else 2_000,
    )
    run_dir = tmp_path / failure

    result = run_mock_identification(
        cfg, observer, supervisor, run_dir, clock, clock.sleep
    )

    assert result.status is RunStatus.ABORTED
    commands = jsonl(run_dir, "commands.jsonl")
    normal = [item for item in commands if item["authorization_kind"] == "normal"]
    recovery = [item for item in commands if item["authorization_kind"] == "recovery"]
    expected_normal_count = (
        0 if failure == "timeout" else (1 if failure == "variance" else 2)
    )
    assert len(normal) == expected_normal_count
    assert recovery[-1]["normalized_position"] == 0.0
    assert supervisor.state is RunState.FAULTED
    assert supervisor.safe_state_verified is True
    assert all(
        item["authorization_kind"] in {"normal", "recovery"} for item in commands
    )
    assert jsonl(run_dir, "faults.jsonl")


def test_controller_fault_is_recorded_before_abort_and_no_next_normal_command(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    run_dir = tmp_path / "fault"

    result = run_mock_identification(
        config(manifest, mock_behavior={"fault_on_calls": [2]}),
        RecordingObserver(clock),
        supervisor,
        run_dir,
        clock,
        clock.sleep,
    )

    assert result.status is RunStatus.ABORTED
    normal = [
        item
        for item in jsonl(run_dir, "commands.jsonl")
        if item["authorization_kind"] == "normal"
    ]
    assert [item["normalized_position"] for item in normal] == [0.0, 0.1]
    assert any(
        item["fault"]["code"] == "controller-error"
        for item in jsonl(run_dir, "faults.jsonl")
    )
    recovery_steps = {
        item["step_id"]
        for item in jsonl(run_dir, "commands.jsonl")
        if item["authorization_kind"] == "recovery"
    }
    assert recovery_steps
    assert recovery_steps <= {
        item["step_id"] for item in jsonl(run_dir, "observations.jsonl")
    }
    assert recovery_steps <= {
        item["step_id"]
        for item in jsonl(run_dir, "transitions.jsonl")
        if item["operation"] == "home-verified"
    }
    assert any(
        item["operation"] == "retry-recovery"
        for item in jsonl(run_dir, "transitions.jsonl")
    )
    assert supervisor.state is RunState.FAULTED
    assert supervisor.safe_state_verified is True


def test_observer_exception_aborts_and_sanitizes_failure(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    run_dir = tmp_path / "observer-error"

    result = run_mock_identification(
        config(manifest),
        RaisingObserver(clock),
        supervisor,
        run_dir,
        clock,
        clock.sleep,
    )

    assert result.status is RunStatus.ABORTED
    manifest_text = (run_dir / "manifest.json").read_text()
    assert "raw camera details" not in manifest_text
    assert result.failure is not None
    assert result.failure.error_type == "ObserverError"


def test_unknown_adapter_application_is_not_inferred_as_home(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    run_dir = tmp_path / "unknown-application"

    result = run_mock_identification(
        config(
            manifest,
            mock_behavior={"raise_after_authorization_calls": [2]},
        ),
        RecordingObserver(clock),
        supervisor,
        run_dir,
        clock,
        clock.sleep,
    )

    assert result.status is RunStatus.ABORTED
    assert supervisor.state is RunState.FAULTED
    assert supervisor.safe_state_verified is False
    assert all(
        item["authorization_kind"] == "normal"
        for item in jsonl(run_dir, "commands.jsonl")
    )
    transitions = jsonl(run_dir, "transitions.jsonl")
    assert any(item["operation"] == "recovery-unavailable" for item in transitions)
    assert not any(
        item["operation"] == "home-verified" and "recovery" in item["step_id"]
        for item in transitions
    )


def test_runner_derives_controller_settling_instead_of_trusting_adapter_flag(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    run_dir = tmp_path / "lying-settling"

    result = run_mock_identification(
        config(
            manifest,
            mock_behavior={"controller_output_offset_qus_by_call": {1: 1}},
        ),
        RecordingObserver(clock),
        supervisor,
        run_dir,
        clock,
        clock.sleep,
    )

    assert result.status is RunStatus.ABORTED
    assert result.failure is not None
    assert result.failure.category == "controller_error"
    decision = jsonl(run_dir, "controller-settling.jsonl")[0]["decision"]
    assert decision["target_reached"] is False


def test_public_runner_has_no_adapter_injection_path(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    assert not hasattr(system_identification, "run_identification")
    assert "adapter" not in inspect.signature(run_mock_identification).parameters
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    deceptive = HardwareCapableFake(
        manifest=manifest,
        clock=clock,
        permit_verifier=supervisor.actuation_permit_verifier,
    )

    with pytest.raises(TypeError, match="adapter"):
        run_mock_identification(
            config=config(manifest),
            observer=RecordingObserver(clock),
            supervisor=supervisor,
            adapter=deceptive,  # type: ignore[call-arg]
            output_dir=tmp_path / "must-not-run",
            clock=clock,
            sleeper=clock.sleep,
        )

    assert supervisor.state is RunState.ARMED
    assert deceptive.applied_wrappers == []


def test_observer_provenance_failure_is_sanitized_and_cancels_armed_run(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    run_dir = tmp_path / "provenance-failure"

    result = run_mock_identification(
        config(manifest),
        RaisingProvenanceObserver(clock),
        supervisor,
        run_dir,
        clock,
        clock.sleep,
    )

    assert result.status is RunStatus.ABORTED
    assert result.failure is not None
    assert result.failure.category == "camera_loss"
    assert result.failure.error_type == "ObserverProvenanceError"
    assert supervisor.state is RunState.DISARMED
    serialized_manifest = (run_dir / "manifest.json").read_text()
    assert "secret camera path" not in serialized_manifest
    assert "participant detail" not in serialized_manifest
    metadata = result.identification_metadata
    assert metadata is not None
    assert metadata.observer is None
    assert metadata.expected_observer == config(manifest).observer


def test_runtime_observer_identity_is_rejected_before_start_or_apply(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    result = run_mock_identification(
        config(manifest),
        WrongIdentityObserver(clock),
        supervisor,
        tmp_path / "observer-identity-rejected",
        clock,
        clock.sleep,
    )

    assert result.status is RunStatus.ABORTED
    assert result.failure is not None
    assert result.failure.category == "camera_loss"
    assert supervisor.state is RunState.DISARMED


def test_supervisor_manifest_global_preflight_difference_cancels_armed_run(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    requirement = manifest.preflight_requirements[0]
    changed = manifest.model_copy(
        update={
            "preflight_requirements": (
                requirement.model_copy(update={"description": "changed"}),
                *manifest.preflight_requirements[1:],
            )
        }
    )
    assert changed.calibration_sha256 == manifest.calibration_sha256
    clock = FakeClock()
    supervisor = armed_supervisor(changed, clock)

    result = run_mock_identification(
        config(manifest),
        RecordingObserver(clock),
        supervisor,
        tmp_path / "full-manifest-mismatch",
        clock,
        clock.sleep,
    )

    assert result.status is RunStatus.ABORTED
    assert supervisor.state is RunState.DISARMED
    assert result.failure is not None
    assert result.failure.category == "safety_error"


def test_watchdog_runs_after_observer_and_prevents_next_movement(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    run_dir = tmp_path / "watchdog-observer"

    result = run_mock_identification(
        config(manifest, step_timeout_ms=20_000),
        SlowObserver(clock),
        supervisor,
        run_dir,
        clock,
        clock.sleep,
    )

    assert result.status is RunStatus.ABORTED
    normal = [
        item
        for item in jsonl(run_dir, "commands.jsonl")
        if item["authorization_kind"] == "normal"
    ]
    assert len(normal) == 1
    assert any(
        item["fault"]["code"] == "watchdog-expired"
        for item in jsonl(run_dir, "faults.jsonl")
    )


def test_sleeper_failure_after_first_motion_aborts_recovers_and_publishes(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    run_dir = tmp_path / "sleeper-failure"
    failing_sleeper = FailingOnceSleeper(clock, fail_on_call=2)

    result = run_mock_identification(
        config(manifest),
        RecordingObserver(clock),
        supervisor,
        run_dir,
        clock,
        failing_sleeper,
    )

    assert result.status is RunStatus.ABORTED
    assert result.failure is not None
    assert result.failure.category == "timeout"
    assert supervisor.state is RunState.FAULTED
    assert (run_dir / "manifest.json").is_file()
    assert "private sleeper detail" not in (run_dir / "manifest.json").read_text()


def test_stable_nonbaseline_home_aborts_before_next_movement(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    run_dir = tmp_path / "false-home"
    observer = StepObserver(
        clock,
        {"step-0001": 0.20, "step-0002": 0.50, "step-0003": 0.25},
    )

    result = run_mock_identification(
        config(manifest),
        observer,
        supervisor,
        run_dir,
        clock,
        clock.sleep,
    )

    assert result.status is RunStatus.ABORTED
    normal = [
        item["normalized_position"]
        for item in jsonl(run_dir, "commands.jsonl")
        if item["authorization_kind"] == "normal"
    ]
    assert normal == [0.0, 0.1, 0.0]
    assert result.failure is not None
    assert result.failure.category == "visual_error"


def test_start_abort_uses_recovery_driver_and_exits_terminal(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    clock.sleep(60.0)

    result = run_mock_identification(
        config(manifest),
        RecordingObserver(clock),
        supervisor,
        tmp_path / "start-abort",
        clock,
        clock.sleep,
    )

    assert result.status is RunStatus.ABORTED
    assert supervisor.state is RunState.FAULTED
    assert any(
        item["authorization_kind"] == "recovery"
        for item in jsonl(tmp_path / "start-abort", "commands.jsonl")
    )


def test_recovery_deadline_exhaustion_fails_closed(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)
    run_dir = tmp_path / "recovery-exhausted"

    result = run_mock_identification(
        config(
            manifest,
            recovery_timeout_ms=1,
            maximum_recovery_attempts=1,
            mock_behavior={"fault_on_calls": [2]},
        ),
        RecordingObserver(clock),
        supervisor,
        run_dir,
        clock,
        clock.sleep,
    )

    assert result.status is RunStatus.ABORTED
    assert result.failure is not None
    assert result.failure.category == "recovery_error"
    assert supervisor.state is RunState.FAULTED
    assert supervisor.safe_state_verified is False
    assert any(
        item["fault"]["code"] == "recovery-exhausted"
        for item in jsonl(run_dir, "faults.jsonl")
    )


def test_run_requires_armed_supervisor_and_does_not_move(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    clock = FakeClock()
    supervisor = SafetySupervisor(
        manifest=manifest,
        limits=SafetyLimits(
            max_step=0.2,
            max_rate_per_second=1.0,
            max_acceleration_per_second_squared=10.0,
            watchdog_timeout_ns=5_000_000_000,
            approval_max_age_ns=60_000_000_000,
            preflight_max_age_ns=60_000_000_000,
            command_max_age_ns=100_000_000,
            recovery_command_ttl_ns=1_000_000_000,
        ),
        clock=clock,
    )
    result = run_mock_identification(
        config(manifest),
        RecordingObserver(clock),
        supervisor,
        tmp_path / "disarmed",
        clock,
        clock.sleep,
    )

    assert result.status is RunStatus.ABORTED
    assert jsonl(tmp_path / "disarmed", "commands.jsonl") == []


def test_output_directory_is_immutable(
    tmp_path: Path, manifest: HardwareManifest
) -> None:
    run_dir = tmp_path / "existing"
    run_dir.mkdir()
    (run_dir / "evidence.txt").write_text("keep")
    clock = FakeClock()
    supervisor = armed_supervisor(manifest, clock)

    with pytest.raises(FileExistsError):
        run_mock_identification(
            config(manifest),
            RecordingObserver(clock),
            supervisor,
            run_dir,
            clock,
            clock.sleep,
        )

    assert (run_dir / "evidence.txt").read_text() == "keep"
