"""Integrity-checked, hardware-independent streaming motion model packages."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from safetensors.torch import load_file

from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.models.head_scheduler import HeadGestureConfig, HeadGestureScheduler
from alice.models.residual_state_space import (
    ResidualStateSpace,
    ResidualStateSpaceConfig,
)
from alice.motion.controller_response import (
    ControllerResponse,
    ControllerResponseConfig,
)
from alice.motion.face_events import FaceEventConfig, FaceEventGenerator

_WEIGHTS_PATH = "weights/residual.safetensors"
_TRAINING_RECORD_PATH = "records/training.json"
_CONFIG_PATHS = {
    "controller_response": "configs/controller_response.json",
    "face_events": "configs/face_events.json",
    "head_gestures": "configs/head_gestures.json",
    "residual": "configs/residual.json",
}
_MANIFEST_PATH = "manifest.json"
_DECLARED_ARTIFACT_PATHS = frozenset(
    {_WEIGHTS_PATH, _TRAINING_RECORD_PATH, *_CONFIG_PATHS.values()}
)
_TRAINING_RECORD_FIELDS = frozenset(
    {"schema_version", "model", "training", "run", "dataset", "artifact", "epochs"}
)
_TRAINING_FIELDS = frozenset(
    {
        "epochs",
        "learning_rate",
        "rollout_steps",
        "response_rate_per_s",
        "loss_weights",
    }
)
_LOSS_FIELDS = frozenset(
    {
        "reconstruction",
        "multistep_rollout",
        "anchor_drift",
        "boundary_continuity",
        "realized_velocity",
        "realized_acceleration",
        "realized_jerk",
    }
)
_RUN_FIELDS = frozenset({"seed", "device", "disposition", "note"})
_DATASET_FIELDS = frozenset(
    {"dataset_id", "split", "split_id", "input_data_reference", "permitted_use"}
)
_ARTIFACT_FIELDS = frozenset({"format", "path", "weights_sha256"})
_EPOCH_FIELDS = frozenset({"epoch", "losses"})
_EPOCH_LOSS_FIELDS = _LOSS_FIELDS | {"total"}


class PackageIdentities(BaseModel):
    """External identities that must agree before any model is constructed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    affect_schema_id: NonEmptyString
    motion_model_id: NonEmptyString
    calibration_sha256: Sha256Hex
    controller_response_model_id: NonEmptyString
    controller_settings_sha256: Sha256Hex


class SeedPolicy(BaseModel):
    """Reproducible training and stateful runtime seed ownership."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["motion-seed-policy/v1"]
    training_seed: Annotated[int, Field(ge=0)]
    runtime_seed_source: Literal["serialized-generator-state"]
    deterministic_replay: Literal[True]


class MotionModelComponents(BaseModel):
    """Offline artifacts and fully resolved configs included in one package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    weights: Path
    residual_config: ResidualStateSpaceConfig
    face_event_config: FaceEventConfig
    head_gesture_config: HeadGestureConfig
    controller_response_config: ControllerResponseConfig


class MotionModelMetadata(BaseModel):
    """Research and runtime metadata retained beside package components."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    identities: PackageIdentities
    training_record: dict[NonEmptyString, JsonValue]
    metrics_reference: NonEmptyString
    seed_policy: SeedPolicy


class _PackageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["motion-model-package/v1"]
    identities: PackageIdentities
    weights: Literal["weights/residual.safetensors"]
    configs: dict[NonEmptyString, NonEmptyString]
    training_record: Literal["records/training.json"]
    metrics_reference: NonEmptyString
    seed_policy: SeedPolicy
    checksums: dict[NonEmptyString, Sha256Hex]

    @model_validator(mode="after")
    def validate_artifact_inventory(self) -> _PackageManifest:
        if self.configs != _CONFIG_PATHS:
            raise ValueError("package config inventory mismatch")
        if set(self.checksums) != _DECLARED_ARTIFACT_PATHS:
            raise ValueError("package checksum inventory mismatch")
        return self


@dataclass(frozen=True, slots=True)
class MotionModelPackage:
    """Filesystem locations for one saved package."""

    path: Path
    manifest: Path
    weights: Path
    configs: Mapping[str, Path]
    training_record: Path


@dataclass(frozen=True, slots=True)
class LoadedMotionModel:
    """Validated software-only components ready for streaming composition."""

    identities: PackageIdentities
    residual_model: ResidualStateSpace
    face_event_generator: FaceEventGenerator
    head_gesture_scheduler: HeadGestureScheduler
    controller_response: ControllerResponse
    training_record: dict[str, JsonValue]
    metrics_reference: str
    seed_policy: SeedPolicy


def save_package(
    path: str | Path,
    components: MotionModelComponents,
    metadata: MotionModelMetadata,
) -> MotionModelPackage:
    """Save an allowlisted package after validating identities and provenance."""

    package_path = Path(path)
    if package_path.exists():
        raise FileExistsError(f"model package already exists: {package_path}")
    if not components.weights.is_file():
        raise ValueError("safetensors weights file does not exist")

    weights = load_file(components.weights, device="cpu")
    weights_sha256 = _sha256(components.weights)
    _validate_components(components, metadata.identities)
    _validate_training_record(
        metadata.training_record,
        residual_config=components.residual_config,
        weights_sha256=weights_sha256,
        seed_policy=metadata.seed_policy,
    )

    probe = ResidualStateSpace(components.residual_config)
    probe.load_state_dict(weights, strict=True)

    package_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{package_path.name}-",
        dir=package_path.parent,
    ) as temporary:
        staged = Path(temporary) / package_path.name
        staged.mkdir()
        artifact_documents: dict[str, dict[str, JsonValue]] = {
            _CONFIG_PATHS["residual"]: components.residual_config.model_dump(
                mode="json"
            ),
            _CONFIG_PATHS["face_events"]: components.face_event_config.model_dump(
                mode="json"
            ),
            _CONFIG_PATHS["head_gestures"]: components.head_gesture_config.model_dump(
                mode="json"
            ),
            _CONFIG_PATHS[
                "controller_response"
            ]: components.controller_response_config.model_dump(mode="json"),
            _TRAINING_RECORD_PATH: metadata.training_record,
        }
        for relative_path, document in artifact_documents.items():
            destination = staged / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            _write_json(destination, document)

        packaged_weights = staged / _WEIGHTS_PATH
        packaged_weights.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(components.weights, packaged_weights)

        checksums = {
            relative_path: _sha256(staged / relative_path)
            for relative_path in sorted(_DECLARED_ARTIFACT_PATHS)
        }
        manifest = _PackageManifest(
            schema_version="motion-model-package/v1",
            identities=metadata.identities,
            weights="weights/residual.safetensors",
            configs=dict(_CONFIG_PATHS),
            training_record="records/training.json",
            metrics_reference=metadata.metrics_reference,
            seed_policy=metadata.seed_policy,
            checksums=checksums,
        )
        _write_json(staged / _MANIFEST_PATH, manifest.model_dump(mode="json"))
        staged.replace(package_path)

    return _package_paths(package_path)


def load_package(
    path: str | Path,
    expected_identities: PackageIdentities | Mapping[str, object],
) -> LoadedMotionModel:
    """Validate a package completely, then construct software model components."""

    package = _package_paths(Path(path))
    expected = PackageIdentities.model_validate(expected_identities)
    manifest = _load_manifest(package.manifest)
    _validate_expected_identities(manifest.identities, expected)
    _validate_package_inventory(package.path)
    _validate_checksums(package.path, manifest.checksums)

    residual_config = ResidualStateSpaceConfig.model_validate(
        _read_json(package.configs["residual"])
    )
    face_event_config = FaceEventConfig.model_validate(
        _read_json(package.configs["face_events"])
    )
    head_gesture_config = HeadGestureConfig.model_validate(
        _read_json(package.configs["head_gestures"])
    )
    controller_config = ControllerResponseConfig.model_validate(
        _read_json(package.configs["controller_response"])
    )
    components = MotionModelComponents(
        weights=package.weights,
        residual_config=residual_config,
        face_event_config=face_event_config,
        head_gesture_config=head_gesture_config,
        controller_response_config=controller_config,
    )
    _validate_components(components, manifest.identities)

    training_record = _read_json(package.training_record)
    _validate_training_record(
        training_record,
        residual_config=residual_config,
        weights_sha256=manifest.checksums[_WEIGHTS_PATH],
        seed_policy=manifest.seed_policy,
    )
    weights = load_file(package.weights, device="cpu")

    residual_model = ResidualStateSpace(residual_config)
    residual_model.load_state_dict(weights, strict=True)
    residual_model.eval()
    controller_response = ControllerResponse(config=controller_config)
    face_event_generator = FaceEventGenerator(
        config=face_event_config,
        controller_config=controller_config,
    )
    head_gesture_scheduler = HeadGestureScheduler(
        config=head_gesture_config,
        controller_config=controller_config,
    )
    return LoadedMotionModel(
        identities=manifest.identities,
        residual_model=residual_model,
        face_event_generator=face_event_generator,
        head_gesture_scheduler=head_gesture_scheduler,
        controller_response=controller_response,
        training_record=training_record,
        metrics_reference=manifest.metrics_reference,
        seed_policy=manifest.seed_policy,
    )


def _validate_components(
    components: MotionModelComponents,
    identities: PackageIdentities,
) -> None:
    configs = (
        components.residual_config,
        components.face_event_config,
        components.head_gesture_config,
    )
    for config in configs:
        _require_identity(
            "affect schema", config.affect_schema_id, identities.affect_schema_id
        )
        _require_identity(
            "calibration", config.calibration_sha256, identities.calibration_sha256
        )
        _require_identity(
            "controller-response model",
            config.controller_response_model_id,
            identities.controller_response_model_id,
        )
        _require_identity(
            "controller settings",
            config.controller_settings_sha256,
            identities.controller_settings_sha256,
        )
    _require_identity(
        "controller-response model",
        components.controller_response_config.model_id,
        identities.controller_response_model_id,
    )
    _require_identity(
        "calibration",
        components.controller_response_config.calibration_sha256,
        identities.calibration_sha256,
    )
    response_actuators = tuple(
        actuator.actuator_name
        for actuator in components.controller_response_config.actuators
    )
    if components.residual_config.actuator_names != response_actuators:
        raise ValueError(
            "residual and controller-response actuator identities mismatch"
        )


def _validate_expected_identities(
    packaged: PackageIdentities,
    expected: PackageIdentities,
) -> None:
    labels = {
        "affect_schema_id": "affect schema",
        "motion_model_id": "motion model",
        "calibration_sha256": "calibration",
        "controller_response_model_id": "controller-response model",
        "controller_settings_sha256": "controller settings",
    }
    for field, label in labels.items():
        _require_identity(label, getattr(packaged, field), getattr(expected, field))


def _require_identity(label: str, actual: str, expected: str) -> None:
    if actual != expected:
        raise ValueError(f"{label} identity mismatch")


def _validate_training_record(
    record: Mapping[str, JsonValue],
    *,
    residual_config: ResidualStateSpaceConfig,
    weights_sha256: str,
    seed_policy: SeedPolicy,
) -> None:
    _require_record_fields(record, _TRAINING_RECORD_FIELDS, section="root")
    if record.get("schema_version") != "residual-training-record/v1":
        raise ValueError("training record schema identity mismatch")
    if record.get("model") != residual_config.model_dump(mode="json"):
        raise ValueError("training record model config identity mismatch")

    training = _record_section(record.get("training"), section="training")
    _require_record_fields(training, _TRAINING_FIELDS, section="training")
    loss_weights = _record_section(
        training.get("loss_weights"), section="training.loss_weights"
    )
    _require_record_fields(loss_weights, _LOSS_FIELDS, section="training.loss_weights")

    run = _record_section(record.get("run"), section="run")
    _require_record_fields(run, _RUN_FIELDS, section="run")
    if run.get("seed") != seed_policy.training_seed:
        raise ValueError("training record seed and package seed policy mismatch")
    if run.get("device") != "cpu":
        raise ValueError("training record device must be cpu")

    dataset = _record_section(record.get("dataset"), section="dataset")
    _require_record_fields(dataset, _DATASET_FIELDS, section="dataset")

    artifact = _record_section(record.get("artifact"), section="artifact")
    _require_record_fields(artifact, _ARTIFACT_FIELDS, section="artifact")
    if artifact.get("format") != "safetensors":
        raise ValueError("training record artifact format must be safetensors")
    if artifact.get("weights_sha256") != weights_sha256:
        raise ValueError("training record weights checksum mismatch")

    epoch_count = training.get("epochs")
    epochs = record.get("epochs")
    if isinstance(epoch_count, bool) or not isinstance(epoch_count, int):
        raise ValueError("training record epoch count must be an integer")
    if not isinstance(epochs, list) or len(epochs) != epoch_count:
        raise ValueError("training record epoch history is incomplete")
    for expected_epoch, value in enumerate(epochs, start=1):
        epoch = _record_section(value, section="epoch")
        _require_record_fields(epoch, _EPOCH_FIELDS, section="epoch")
        if epoch.get("epoch") != expected_epoch:
            raise ValueError("training record epoch numbering mismatch")
        losses = _record_section(epoch.get("losses"), section="epoch.losses")
        _require_record_fields(losses, _EPOCH_LOSS_FIELDS, section="epoch.losses")


def _record_section(value: JsonValue | None, *, section: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"training record {section} section is missing")
    return value


def _require_record_fields(
    value: Mapping[str, JsonValue],
    expected: frozenset[str],
    *,
    section: str,
) -> None:
    if set(value) != expected:
        raise ValueError(
            f"training record contains missing or undeclared fields in {section}"
        )


def _load_manifest(path: Path) -> _PackageManifest:
    try:
        return _PackageManifest.model_validate(_read_json(path))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("model package manifest is unreadable") from error


def _validate_package_inventory(package_path: Path) -> None:
    if not package_path.is_dir() or package_path.is_symlink():
        raise ValueError("model package path must be a real directory")
    if any(path.is_symlink() for path in package_path.rglob("*")):
        raise ValueError("model package must not contain symbolic links")
    actual_files = {
        path.relative_to(package_path).as_posix()
        for path in package_path.rglob("*")
        if path.is_file()
    }
    expected_files = {*_DECLARED_ARTIFACT_PATHS, _MANIFEST_PATH}
    if actual_files != expected_files:
        raise ValueError("model package contains undeclared artifacts")


def _validate_checksums(package_path: Path, checksums: Mapping[str, str]) -> None:
    for relative_path in sorted(_DECLARED_ARTIFACT_PATHS):
        artifact = package_path / relative_path
        if _sha256(artifact) != checksums[relative_path]:
            raise ValueError(f"artifact checksum mismatch: {relative_path}")


def _package_paths(path: Path) -> MotionModelPackage:
    return MotionModelPackage(
        path=path,
        manifest=path / _MANIFEST_PATH,
        weights=path / _WEIGHTS_PATH,
        configs=MappingProxyType(
            {name: path / relative for name, relative in _CONFIG_PATHS.items()}
        ),
        training_record=path / _TRAINING_RECORD_PATH,
    )


def _read_json(path: Path) -> dict[str, JsonValue]:
    with path.open("r", encoding="utf-8") as stream:
        document = json.load(stream)
    if not isinstance(document, dict):
        raise ValueError(f"JSON document must contain an object: {path.name}")
    return document


def _write_json(path: Path, document: Mapping[str, JsonValue]) -> None:
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
