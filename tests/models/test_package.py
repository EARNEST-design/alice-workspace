"""Behavioral tests for integrity-checked, hardware-independent model packages."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import IO, Any

import pytest
import serial
import torch
from safetensors.torch import load_file, save_file

import alice.models.package as package_module
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
from alice.motion.controller_response import load_controller_response_config
from alice.motion.face_events import load_face_event_config

ROOT = Path(__file__).parents[2]
MODEL_CONFIG = ROOT / "config" / "models" / "residual-state-space-v1.yaml"
FACE_CONFIG = ROOT / "config" / "models" / "face-events-v1.yaml"
HEAD_CONFIG = ROOT / "config" / "models" / "head-gestures-v1.yaml"
CONTROLLER_CONFIG = ROOT / "config" / "models" / "maestro-response-v1.yaml"


def _identities() -> PackageIdentities:
    residual = load_residual_state_space_config(MODEL_CONFIG)
    return PackageIdentities(
        affect_schema_id=residual.affect_schema_id,
        motion_model_id="streaming-affect-motion-test-v1",
        calibration_sha256=residual.calibration_sha256,
        controller_response_model_id=residual.controller_response_model_id,
        controller_settings_sha256=residual.controller_settings_sha256,
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
    )
    training_record = {
        "schema_version": "residual-training-record/v1",
        "model": residual.model_dump(mode="json"),
        "training": {
            "epochs": 2,
            "learning_rate": 0.001,
            "rollout_steps": 4,
            "response_rate_per_s": 5.0,
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


def test_loading_restores_exact_weights_in_inference_mode(tmp_path: Path) -> None:
    """Ignoring stored tensors or leaving training mode enabled breaks deployment."""

    package = _write_valid_package(tmp_path)
    expected_weights = load_file(package.weights, device="cpu")

    loaded = load_package(package.path, _identities().model_dump())

    assert not loaded.residual_model.training
    assert set(loaded.residual_model.state_dict()) == set(expected_weights)
    for name, expected in expected_weights.items():
        assert torch.equal(loaded.residual_model.state_dict()[name], expected)


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
