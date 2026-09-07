"""Behavioral tests for integrity-checked, hardware-independent model packages."""

from __future__ import annotations

import hashlib
import json
import math
import os
from copy import deepcopy
from pathlib import Path
from typing import IO, Any

import pytest
import serial
import torch
from safetensors.torch import load_file, save_file

import alice.models.package as package_module
from alice.contracts.motion import TargetUpdate
from alice.models.head_scheduler import load_head_gesture_config
from alice.models.package import (
    MotionModelComponents,
    MotionModelMetadata,
    PackageIdentities,
    SeedPolicy,
    load_package,
    save_package,
)
from alice.models.residual_state_space import (
    ResidualStateSpace,
    load_residual_state_space_config,
)
from alice.motion.anchors import load_procedural_motion_config
from alice.motion.controller_response import load_controller_response_config
from alice.motion.face_events import load_face_event_config
from alice.motion.intent_filter import FilteredIntent, SupportStatus
from alice.motion.state import ActuatorVelocity, GeneratorState, dump_state, load_state
from alice.motion.streaming import ProductionCandidateComposer, StreamingMotionGenerator

ROOT = Path(__file__).parents[2]
MODEL_CONFIG = ROOT / "config" / "models" / "residual-state-space-v1.yaml"
FACE_CONFIG = ROOT / "config" / "models" / "face-events-v1.yaml"
HEAD_CONFIG = ROOT / "config" / "models" / "head-gestures-v1.yaml"
CONTROLLER_CONFIG = ROOT / "config" / "models" / "maestro-response-v1.yaml"
ANCHOR_CONFIG = ROOT / "config" / "models" / "procedural-motion-v1.yaml"


def _identities() -> PackageIdentities:
    residual = load_residual_state_space_config(MODEL_CONFIG)
    controller_response = load_controller_response_config(CONTROLLER_CONFIG)
    return PackageIdentities(
        affect_schema_id=residual.affect_schema_id,
        motion_model_id="streaming-affect-motion-test-v1",
        calibration_sha256=residual.calibration_sha256,
        controller_response_model_id=residual.controller_response_model_id,
        controller_settings_sha256=residual.controller_settings_sha256,
        controller_response_sha256=controller_response.response_sha256,
    )


def _write_source_weights(path: Path) -> tuple[Path, str]:
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(29)
        model = ResidualStateSpace(load_residual_state_space_config(MODEL_CONFIG))
    weights = path / "source.safetensors"
    save_file(
        {
            name: tensor.detach().cpu().contiguous()
            for name, tensor in model.state_dict().items()
        },
        weights,
    )
    return weights, hashlib.sha256(weights.read_bytes()).hexdigest()


def _valid_inputs(
    tmp_path: Path,
) -> tuple[MotionModelComponents, MotionModelMetadata]:
    weights, weights_sha256 = _write_source_weights(tmp_path)
    residual = load_residual_state_space_config(MODEL_CONFIG)
    components = MotionModelComponents(
        weights=weights,
        residual_config=residual,
        face_event_config=load_face_event_config(FACE_CONFIG),
        head_gesture_config=load_head_gesture_config(HEAD_CONFIG),
        controller_response_config=load_controller_response_config(CONTROLLER_CONFIG),
        anchor_config=load_procedural_motion_config(ANCHOR_CONFIG),
    )
    training_record = {
        "schema_version": "residual-training-record/v2",
        "model": residual.model_dump(mode="json"),
        "training": {
            "epochs": 2,
            "learning_rate": 0.001,
            "rollout_steps": 4,
            "controller_settings_sha256": residual.controller_settings_sha256,
            "controller_response_sha256": (
                components.controller_response_config.response_sha256
            ),
            "loss_weights": {
                "reconstruction": 1.0,
                "multistep_rollout": 0.5,
                "anchor_drift": 0.2,
                "boundary_continuity": 0.5,
                "realized_velocity": 0.05,
                "realized_acceleration": 0.02,
                "realized_jerk": 0.01,
            },
        },
        "run": {
            "seed": 29,
            "device": "cpu",
            "disposition": "keep",
            "note": "Synthetic package test; no participant data.",
        },
        "dataset": {
            "dataset_id": "synthetic-package-fixture-v1",
            "split": "train",
            "split_id": "fixture-train-v1",
            "input_data_reference": "synthetic://package-test-v1",
            "permitted_use": "Automated tests only.",
        },
        "artifact": {
            "format": "safetensors",
            "path": weights.name,
            "weights_sha256": weights_sha256,
        },
        "epochs": [
            {
                "epoch": 1,
                "losses": {
                    "reconstruction": 0.2,
                    "multistep_rollout": 0.2,
                    "anchor_drift": 0.2,
                    "boundary_continuity": 0.2,
                    "realized_velocity": 0.2,
                    "realized_acceleration": 0.2,
                    "realized_jerk": 0.2,
                    "total": 0.2,
                },
            },
            {
                "epoch": 2,
                "losses": {
                    "reconstruction": 0.1,
                    "multistep_rollout": 0.1,
                    "anchor_drift": 0.1,
                    "boundary_continuity": 0.1,
                    "realized_velocity": 0.1,
                    "realized_acceleration": 0.1,
                    "realized_jerk": 0.1,
                    "total": 0.1,
                },
            },
        ],
    }
    metadata = MotionModelMetadata(
        identities=_identities(),
        training_record=training_record,
        metrics_reference="metrics://streaming-affect-motion-test/v1",
        seed_policy=SeedPolicy(
            schema_version="motion-seed-policy/v1",
            training_seed=29,
            runtime_seed_source="serialized-generator-state",
            deterministic_replay=True,
        ),
    )
    return components, metadata


def _write_valid_package(tmp_path: Path) -> package_module.MotionModelPackage:
    components, metadata = _valid_inputs(tmp_path)
    return save_package(tmp_path / "model-package", components, metadata)


def test_modified_weights_are_rejected(tmp_path: Path) -> None:
    """Replacing checked weights must never silently construct another model."""

    package = _write_valid_package(tmp_path)
    package.weights.write_bytes(b"changed")

    with pytest.raises(ValueError, match="checksum"):
        load_package(package.path, _identities())


def test_loading_does_not_construct_hardware(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loading an offline model package must never open a serial adapter."""

    package = _write_valid_package(tmp_path)

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("serial hardware was constructed")

    monkeypatch.setattr(serial, "Serial", fail_if_called)

    loaded = load_package(package.path, _identities())

    assert loaded.identities == _identities()
    assert isinstance(loaded.residual_model, ResidualStateSpace)
    assert isinstance(loaded.candidate_composer, ProductionCandidateComposer)


def test_loaded_composer_replans_deterministically_with_complete_state(
    tmp_path: Path,
) -> None:
    package = _write_valid_package(tmp_path)
    loaded = load_package(package.path, _identities())
    anchor = load_procedural_motion_config(ANCHOR_CONFIG).anchor("neutral")
    target = TargetUpdate(offset_s=0.0, targets=anchor.targets)
    intent = FilteredIntent(
        schema_version="filtered-intent/v1",
        affect_schema_id="affect-vector/v1",
        vector=(0.1, 0.2, -0.1),
        intensity=0.5,
        source_id="package-composer-test",
        accepted_monotonic_ns=0,
        support_status=SupportStatus.SUPPORTED,
        support_distance=0.0,
        reason="synthetic integration fixture",
    )
    rng = __import__("numpy").random.default_rng(0)
    state = GeneratorState(
        schema_version="generator-state/v1",
        last_accepted_target=target,
        last_reported_pose=target,
        estimated_velocity=tuple(
            ActuatorVelocity(actuator_name=t.actuator_name, velocity_per_s=0.0)
            for t in target.targets
        ),
        filtered_intent=intent,
        latent_vector=(0.0,) * loaded.residual_model.config.hidden_size,
        numpy_rng_state=rng.bit_generator.state,
        torch_rng_state=tuple(int(v) for v in torch.random.get_rng_state()),
        event_history=(),
        model_id=_identities().motion_model_id,
        model_sha256=hashlib.sha256(package.manifest.read_bytes()).hexdigest(),
        calibration_sha256=_identities().calibration_sha256,
        controller_settings_sha256=_identities().controller_settings_sha256,
        monotonic_ns=0,
    )
    runtime = StreamingMotionGenerator(
        generator=loaded.candidate_composer, horizon_s=1.0, prefix_duration_s=0.4
    )
    direct_state = state
    replay_state = load_state(dump_state(state))
    previous_ends_ns = 0

    for _ in range(25):
        direct = runtime.replan(
            intent, direct_state, direct_state.monotonic_ns
        )
        replay = runtime.replan(
            intent, replay_state, replay_state.monotonic_ns
        )

        assert direct == replay
        prefix, direct_state = direct
        _, replay_boundary = replay
        assert prefix.starts_at_ns == previous_ends_ns
        assert prefix.ends_at_ns > prefix.starts_at_ns
        assert all(
            math.isfinite(t.normalized_position)
            and -1.0 <= t.normalized_position <= 1.0
            for update in prefix.updates
            for t in update.targets
        )
        assert all(
            left.offset_s < right.offset_s
            for left, right in zip(prefix.updates, prefix.updates[1:])
        )

        replay_state = load_state(dump_state(replay_boundary))
        previous_ends_ns = prefix.ends_at_ns

    assert state.last_accepted_target == target


def test_loading_restores_exact_weights_in_inference_mode(tmp_path: Path) -> None:
    """Ignoring stored tensors or leaving training mode enabled breaks deployment."""

    package = _write_valid_package(tmp_path)
    expected_weights = load_file(package.weights, device="cpu")

    loaded = load_package(package.path, _identities().model_dump())

    assert not loaded.residual_model.training
    assert set(loaded.residual_model.state_dict()) == set(expected_weights)
    for name, expected in expected_weights.items():
        assert torch.equal(loaded.residual_model.state_dict()[name], expected)


@pytest.mark.parametrize("operation", ["save", "load"])
def test_non_finite_weights_are_rejected(tmp_path: Path, operation: str) -> None:
    components, metadata = _valid_inputs(tmp_path)
    tensors = load_file(components.weights)
    first = next(iter(tensors))
    tensors[first].view(-1)[0] = float("nan")
    save_file(tensors, components.weights)
    digest = hashlib.sha256(components.weights.read_bytes()).hexdigest()
    metadata = metadata.model_copy(
        update={
            "training_record": metadata.training_record.model_copy(
                update={
                    "artifact": metadata.training_record.artifact.model_copy(
                        update={"weights_sha256": digest}
                    )
                }
            )
        }
    )
    if operation == "save":
        with pytest.raises(ValueError, match="finite"):
            save_package(tmp_path / "bad", components, metadata)
        return
    other = tmp_path / "other"
    other.mkdir()
    package = save_package(tmp_path / "package", *_valid_inputs(other))
    bad = load_file(package.weights)
    name = next(iter(bad))
    bad[name].view(-1)[0] = float("inf")
    save_file(bad, package.weights)
    payload = package.weights.read_bytes()
    manifest = json.loads(package.manifest.read_text())
    manifest["checksums"]["weights/residual.safetensors"] = hashlib.sha256(
        payload
    ).hexdigest()
    record_path = package.training_record
    record = json.loads(record_path.read_text())
    record["artifact"]["weights_sha256"] = manifest["checksums"][
        "weights/residual.safetensors"
    ]
    record_path.write_text(json.dumps(record, sort_keys=True) + "\n")
    manifest["checksums"]["records/training.json"] = hashlib.sha256(
        record_path.read_bytes()
    ).hexdigest()
    package.manifest.write_text(json.dumps(manifest, sort_keys=True) + "\n")
    with pytest.raises(ValueError, match="finite"):
        load_package(package.path, _identities())


def test_embedded_controller_settings_are_bound_to_identity(tmp_path: Path) -> None:
    components, metadata = _valid_inputs(tmp_path)
    changed = components.controller_response_config.model_copy(
        update={
            "actuators": (
                components.controller_response_config.actuators[0].model_copy(
                    update={"firmware_speed_setting": 21}
                ),
                *components.controller_response_config.actuators[1:],
            )
        }
    )
    with pytest.raises(ValueError, match="embedded controller settings"):
        save_package(
            tmp_path / "mismatch",
            components.model_copy(update={"controller_response_config": changed}),
            metadata,
        )


def test_exact_controller_response_is_bound_before_model_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing fitted speed alone must not retain package compatibility."""

    components, metadata = _valid_inputs(tmp_path)
    controller = components.controller_response_config
    neck_index = next(
        index
        for index, actuator in enumerate(controller.actuators)
        if actuator.actuator_name == "neck_rotation"
    )
    neck = controller.actuators[neck_index]
    changed_neck = neck.model_copy(update={"max_velocity_per_s": 0.4})
    changed_actuators = list(controller.actuators)
    changed_actuators[neck_index] = changed_neck
    changed_controller = controller.model_copy(
        update={"actuators": tuple(changed_actuators)}
    )
    assert neck.max_velocity_per_s == 0.8
    assert (
        changed_controller.controller_settings_sha256
        == controller.controller_settings_sha256
    )
    assert changed_controller.response_sha256 != controller.response_sha256
    monkeypatch.setattr(package_module, "ResidualStateSpace", _fail_if_constructed)

    with pytest.raises(ValueError, match="controller response identity mismatch"):
        save_package(
            tmp_path / "response-mismatch",
            components.model_copy(
                update={"controller_response_config": changed_controller}
            ),
            metadata,
        )


def test_training_response_identity_is_bound_before_model_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Weights trained against another response must not enter model setup."""

    components, metadata = _valid_inputs(tmp_path)
    controller = components.controller_response_config
    changed_neck = controller.actuator("neck_rotation").model_copy(
        update={"max_velocity_per_s": 0.7}
    )
    changed_controller = controller.model_copy(
        update={
            "actuators": tuple(
                changed_neck
                if actuator.actuator_name == "neck_rotation"
                else actuator
                for actuator in controller.actuators
            )
        }
    )
    changed_metadata = metadata.model_copy(
        update={
            "identities": metadata.identities.model_copy(
                update={
                    "controller_response_sha256": changed_controller.response_sha256
                }
            )
        }
    )
    monkeypatch.setattr(package_module, "ResidualStateSpace", _fail_if_constructed)

    with pytest.raises(
        ValueError,
        match="training controller response identity mismatch",
    ):
        save_package(
            tmp_path / "training-response-mismatch",
            components.model_copy(
                update={"controller_response_config": changed_controller}
            ),
            changed_metadata,
        )


def test_identity_mismatch_is_rejected_before_model_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller expecting another calibration must fail before model setup."""

    package = _write_valid_package(tmp_path)
    expected = _identities().model_copy(update={"calibration_sha256": "f" * 64})

    def fail_if_constructed(*args: object, **kwargs: object) -> None:
        raise AssertionError("model was constructed before identity validation")

    monkeypatch.setattr(package_module, "ResidualStateSpace", fail_if_constructed)

    with pytest.raises(ValueError, match="calibration.*identity mismatch"):
        load_package(package.path, expected)


def test_legacy_package_schema_is_rejected_before_model_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A v1 package cannot prove which response model trained its weights."""

    package = _write_valid_package(tmp_path)
    manifest = json.loads(package.manifest.read_text(encoding="utf-8"))
    manifest["schema_version"] = "motion-model-package/v1"
    package.manifest.write_text(
        json.dumps(manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(package_module, "ResidualStateSpace", _fail_if_constructed)

    with pytest.raises(ValueError, match="manifest is unreadable"):
        load_package(package.path, _identities())


def test_modified_resolved_config_is_rejected(tmp_path: Path) -> None:
    """A changed policy config must not bypass the package integrity boundary."""

    package = _write_valid_package(tmp_path)
    package.configs["face_events"].write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="checksum"):
        load_package(package.path, _identities())


def test_package_contains_only_declared_offline_artifacts(tmp_path: Path) -> None:
    """Saving must not copy datasets, media, credentials, or adapter code."""

    package = _write_valid_package(tmp_path)

    relative_files = {
        path.relative_to(package.path).as_posix()
        for path in package.path.rglob("*")
        if path.is_file()
    }
    assert relative_files == {
        "manifest.json",
        "weights/residual.safetensors",
        "configs/controller_response.json",
        "configs/anchor.json",
        "configs/face_events.json",
        "configs/head_gestures.json",
        "configs/residual.json",
        "records/training.json",
    }

    manifest = json.loads(package.manifest.read_text(encoding="utf-8"))
    assert set(manifest["checksums"]) == relative_files - {"manifest.json"}
    assert manifest["metrics_reference"].startswith("metrics://")
    assert manifest["seed_policy"]["runtime_seed_source"] == (
        "serialized-generator-state"
    )


def test_cross_component_identity_mismatch_leaves_no_package(tmp_path: Path) -> None:
    """Inconsistent resolved components must fail before writing partial output."""

    components, metadata = _valid_inputs(tmp_path)
    face_config = components.face_event_config.model_copy(
        update={"affect_schema_id": "affect-vector/other"}
    )
    components = components.model_copy(update={"face_event_config": face_config})

    with pytest.raises(ValueError, match="affect.*identity mismatch"):
        save_package(tmp_path / "invalid-package", components, metadata)

    assert not (tmp_path / "invalid-package").exists()


def test_training_record_rejects_undeclared_payload_fields(tmp_path: Path) -> None:
    """An allowlisted record filename must not conceal credentials or raw data."""

    components, metadata = _valid_inputs(tmp_path)
    unsafe_record = metadata.training_record.model_dump(mode="python")
    unsafe_record["credentials"] = "must-not-be-packaged"
    unsafe_metadata = metadata.model_copy(update={"training_record": unsafe_record})

    with pytest.raises(ValueError, match="training record.*undeclared"):
        save_package(tmp_path / "unsafe-package", components, unsafe_metadata)

    assert not (tmp_path / "unsafe-package").exists()


def test_load_uses_the_same_weight_bytes_that_passed_checksum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing a path after its hash is read must not replace loaded weights."""

    package = _write_valid_package(tmp_path)
    expected = {
        name: tensor.clone()
        for name, tensor in load_file(package.weights, device="cpu").items()
    }
    replacement = tmp_path / "replacement.safetensors"
    model = ResidualStateSpace(load_residual_state_space_config(MODEL_CONFIG))
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
    save_file(
        {name: tensor.contiguous() for name, tensor in model.state_dict().items()},
        replacement,
    )
    assert replacement.read_bytes() != package.weights.read_bytes()
    original_open = Path.open
    original_os_open = os.open
    swapped = False

    class SwapAfterFirstRead:
        def __init__(self, stream: IO[bytes]) -> None:
            self._stream = stream

        def __enter__(self) -> SwapAfterFirstRead:
            self._stream.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._stream.__exit__(*args)

        def read(self, size: int = -1) -> bytes:
            nonlocal swapped
            data = self._stream.read(size)
            if data and not swapped:
                package.weights.write_bytes(replacement.read_bytes())
                swapped = True
            return data

    def racing_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        stream = original_open(path, *args, **kwargs)
        mode = args[0] if args else kwargs.get("mode", "r")
        if path == package.weights and mode == "rb" and not swapped:
            return SwapAfterFirstRead(stream)
        return stream

    def racing_os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        descriptor = original_os_open(path, flags, *args, **kwargs)
        if Path(path) == package.weights and not swapped:
            os.replace(replacement, package.weights)
            swapped = True
        return descriptor

    monkeypatch.setattr(Path, "open", racing_open)
    monkeypatch.setattr(os, "open", racing_os_open)

    loaded = load_package(package.path, _identities())

    assert swapped
    for name, tensor in expected.items():
        assert torch.equal(loaded.residual_model.state_dict()[name], tensor)


def _fail_if_constructed(*args: object, **kwargs: object) -> None:
    raise AssertionError("component constructed before package validation")


@pytest.mark.parametrize("component_name", ["face", "head"])
def test_affect_dimensions_are_cross_checked_before_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    component_name: str,
) -> None:
    """A shared schema ID must not conceal reordered conditioning dimensions."""

    components, metadata = _valid_inputs(tmp_path)
    if component_name == "face":
        face_config = components.face_event_config.model_copy(
            update={
                "affect_dimensions": tuple(
                    reversed(components.face_event_config.affect_dimensions)
                )
            }
        )
        components = components.model_copy(update={"face_event_config": face_config})
    else:
        head_config = components.head_gesture_config.model_copy(
            update={
                "affect_dimensions": tuple(
                    reversed(components.head_gesture_config.affect_dimensions)
                )
            }
        )
        components = components.model_copy(update={"head_gesture_config": head_config})
    monkeypatch.setattr(package_module, "ResidualStateSpace", _fail_if_constructed)

    with pytest.raises(ValueError, match="affect dimensions"):
        save_package(
            tmp_path / f"invalid-{component_name}-dimensions", components, metadata
        )


@pytest.mark.parametrize(
    "case", ["residual-order", "face-order", "head-membership", "head-order"]
)
def test_actuator_references_are_cross_checked_before_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Face/head references must preserve known semantic actuator membership/order."""

    components, metadata = _valid_inputs(tmp_path)
    if case == "residual-order":
        residual = components.residual_config.model_copy(
            update={
                "actuator_names": (
                    components.residual_config.actuator_names[1],
                    components.residual_config.actuator_names[0],
                    *components.residual_config.actuator_names[2:],
                )
            }
        )
        components = components.model_copy(update={"residual_config": residual})
    elif case == "face-order":
        policy = components.face_event_config.events[0]
        changed_policy = policy.model_copy(
            update={"actuator_names": tuple(reversed(policy.actuator_names))}
        )
        face_config = components.face_event_config.model_copy(
            update={
                "events": (changed_policy, *components.face_event_config.events[1:])
            }
        )
        components = components.model_copy(update={"face_event_config": face_config})
    elif case == "head-membership":
        head = components.head_gesture_config
        unknown = "unlisted_head_axis"
        semantics = head.semantics.model_copy(update={"yaw_actuator_name": unknown})
        recovery = (
            head.recovery_targets[0].model_copy(update={"actuator_name": unknown}),
            *head.recovery_targets[1:],
        )
        gestures = tuple(
            policy.model_copy(update={"actuator_name": unknown})
            if policy.kind.value == "shake"
            else policy
            for policy in head.gestures
        )
        head = head.model_copy(
            update={
                "semantics": semantics,
                "recovery_targets": recovery,
                "gestures": gestures,
            }
        )
        components = components.model_copy(update={"head_gesture_config": head})
    else:
        head = components.head_gesture_config
        semantics = head.semantics.model_copy(
            update={
                "yaw_actuator_name": "head_tilt",
                "tilt_actuator_name": "neck_rotation",
            }
        )
        recovery = (
            head.recovery_targets[0].model_copy(update={"actuator_name": "head_tilt"}),
            head.recovery_targets[1].model_copy(
                update={"actuator_name": "neck_rotation"}
            ),
            *head.recovery_targets[2:],
        )
        gestures = tuple(
            policy.model_copy(
                update={
                    "actuator_name": {
                        "neck_rotation": "head_tilt",
                        "head_tilt": "neck_rotation",
                    }.get(policy.actuator_name, policy.actuator_name)
                }
            )
            for policy in head.gestures
        )
        head = head.model_copy(
            update={
                "semantics": semantics,
                "recovery_targets": recovery,
                "gestures": gestures,
            }
        )
        components = components.model_copy(update={"head_gesture_config": head})
    monkeypatch.setattr(package_module, "ResidualStateSpace", _fail_if_constructed)

    with pytest.raises(ValueError, match="actuator"):
        save_package(tmp_path / f"invalid-{case}", components, metadata)


@pytest.mark.parametrize("component_name", ["face", "head"])
def test_response_feasibility_is_checked_before_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    component_name: str,
) -> None:
    """Resolved event/gesture bounds must fit the packaged response model."""

    components, metadata = _valid_inputs(tmp_path)
    if component_name == "face":
        policy = components.face_event_config.events[0].model_copy(
            update={"onset_s": 0.0001}
        )
        config = components.face_event_config.model_copy(
            update={"events": (policy, *components.face_event_config.events[1:])}
        )
        components = components.model_copy(update={"face_event_config": config})
    else:
        policy = components.head_gesture_config.gestures[0]
        duration = policy.duration_s.model_copy(update={"minimum": 0.0001})
        changed_policy = policy.model_copy(update={"duration_s": duration})
        config = components.head_gesture_config.model_copy(
            update={
                "gestures": (
                    changed_policy,
                    *components.head_gesture_config.gestures[1:],
                )
            }
        )
        components = components.model_copy(update={"head_gesture_config": config})
    monkeypatch.setattr(package_module, "ResidualStateSpace", _fail_if_constructed)

    with pytest.raises(ValueError, match="controller response"):
        save_package(tmp_path / f"infeasible-{component_name}", components, metadata)


@pytest.mark.parametrize(
    ("section", "field", "unsafe_value"),
    [
        (
            "dataset",
            "input_data_reference",
            {"participant_media": [[0.1, 0.2], [0.3, 0.4]]},
        ),
        ("training", "learning_rate", float("nan")),
        ("run", "note", "x" * 10_000),
        ("loss_weights", "reconstruction", -1.0),
        ("epoch_losses", "total", float("inf")),
    ],
    ids=(
        "nested-participant-payload",
        "non-finite-learning-rate",
        "oversized-note",
        "negative-loss-weight",
        "non-finite-epoch-loss",
    ),
)
def test_training_record_values_are_strict_bounded_and_finite(
    tmp_path: Path,
    section: str,
    field: str,
    unsafe_value: object,
) -> None:
    """Typed record fields must not carry blobs or invalid numeric semantics."""

    components, metadata = _valid_inputs(tmp_path)
    record = deepcopy(metadata.training_record.model_dump(mode="python"))
    if section == "loss_weights":
        record["training"]["loss_weights"][field] = unsafe_value
    elif section == "epoch_losses":
        record["epochs"][0]["losses"][field] = unsafe_value
    else:
        record[section][field] = unsafe_value
    metadata = metadata.model_copy(update={"training_record": record})

    with pytest.raises(ValueError, match="training record"):
        save_package(tmp_path / f"unsafe-{section}-{field}", components, metadata)


def test_load_rejects_undeclared_fifo(tmp_path: Path) -> None:
    """A non-regular entry must not evade an inventory that counts only files."""

    package = _write_valid_package(tmp_path)
    os.mkfifo(package.path / "undeclared.pipe")

    with pytest.raises(ValueError, match="non-regular|undeclared"):
        load_package(package.path, _identities())
