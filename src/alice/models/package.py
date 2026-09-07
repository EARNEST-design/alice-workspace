"""Integrity-checked, hardware-independent streaming motion model packages."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal, Mapping, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StringConstraints,
    ValidationError,
    model_validator,
)
from safetensors.torch import load

from alice.contracts.blendshapes import NonEmptyString, Sha256Hex
from alice.models.head_scheduler import (
    HeadGestureConfig,
    HeadGesturePolicy,
    HeadGestureScheduler,
)
from alice.models.residual_state_space import (
    ResidualStateSpace,
    ResidualStateSpaceConfig,
)
from alice.motion.anchors import AnchorPlanner, ProceduralMotionConfig
from alice.motion.controller_response import (
    ActuatorResponseParameters,
    ControllerResponse,
    ControllerResponseConfig,
)
from alice.motion.face_events import (
    FaceEventConfig,
    FaceEventGenerator,
    FaceEventPolicy,
)
from alice.motion.streaming import ProductionCandidateComposer

_WEIGHTS_PATH = "weights/residual.safetensors"
_TRAINING_RECORD_PATH = "records/training.json"
_CONFIG_PATHS = {
    "anchor": "configs/anchor.json",
    "controller_response": "configs/controller_response.json",
    "face_events": "configs/face_events.json",
    "head_gestures": "configs/head_gestures.json",
    "residual": "configs/residual.json",
}
_MANIFEST_PATH = "manifest.json"
_DECLARED_ARTIFACT_PATHS = frozenset(
    {_WEIGHTS_PATH, _TRAINING_RECORD_PATH, *_CONFIG_PATHS.values()}
)
_MAX_EPOCHS = 1_000_000
_MAX_ROLLOUT_STEPS = 1_000_000
_MAX_RATE = 1_000_000.0
_MAX_LOSS = 1_000_000_000_000.0
_HEAD_PEAK_VELOCITY = 15.0 / 8.0
_HEAD_PEAK_ACCELERATION = 10.0 * math.sqrt(3.0) / 3.0

RecordIdentifier = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
RecordText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
]
FinitePositiveRate = Annotated[
    StrictFloat,
    Field(gt=0.0, le=_MAX_RATE, allow_inf_nan=False),
]
FiniteNonNegativeLoss = Annotated[
    StrictFloat,
    Field(ge=0.0, le=_MAX_LOSS, allow_inf_nan=False),
]


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


class ResidualTrainingLossWeights(BaseModel):
    """Strict non-negative objective weights from the Task 2 trainer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reconstruction: FiniteNonNegativeLoss
    multistep_rollout: FiniteNonNegativeLoss
    anchor_drift: FiniteNonNegativeLoss
    boundary_continuity: FiniteNonNegativeLoss
    realized_velocity: FiniteNonNegativeLoss
    realized_acceleration: FiniteNonNegativeLoss
    realized_jerk: FiniteNonNegativeLoss

    @model_validator(mode="after")
    def validate_nonzero_objective(self) -> Self:
        if not any(value > 0.0 for value in self.model_dump().values()):
            raise ValueError("at least one training loss weight must be positive")
        return self


class ResidualTrainingSettings(BaseModel):
    """Bounded resolved optimizer and rollout settings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    epochs: Annotated[StrictInt, Field(ge=1, le=_MAX_EPOCHS)]
    learning_rate: Annotated[StrictFloat, Field(gt=0.0, le=1.0, allow_inf_nan=False)]
    rollout_steps: Annotated[StrictInt, Field(ge=2, le=_MAX_ROLLOUT_STEPS)]
    controller_settings_sha256: Sha256Hex
    loss_weights: ResidualTrainingLossWeights


class ResidualTrainingRun(BaseModel):
    """Bounded, offline run metadata without arbitrary nested payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seed: Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]
    device: Literal["cpu"]
    disposition: Literal["keep", "discard"]
    note: RecordText


class ResidualDatasetReference(BaseModel):
    """Identifiers and consent text only; never training examples or media."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: RecordIdentifier
    split: Literal["train", "validation"]
    split_id: RecordIdentifier
    input_data_reference: RecordText
    permitted_use: RecordText


class ResidualArtifactRecord(BaseModel):
    """Identity of the original safetensors training artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["safetensors"]
    path: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
    ]
    weights_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_filename(self) -> Self:
        if Path(self.path).name != self.path:
            raise ValueError("training artifact path must be a filename")
        return self


class ResidualEpochLosses(BaseModel):
    """Finite non-negative objective values for one completed epoch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reconstruction: FiniteNonNegativeLoss
    multistep_rollout: FiniteNonNegativeLoss
    anchor_drift: FiniteNonNegativeLoss
    boundary_continuity: FiniteNonNegativeLoss
    realized_velocity: FiniteNonNegativeLoss
    realized_acceleration: FiniteNonNegativeLoss
    realized_jerk: FiniteNonNegativeLoss
    total: FiniteNonNegativeLoss


class ResidualEpochRecord(BaseModel):
    """One strictly numbered epoch and its scalar-only losses."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    epoch: Annotated[StrictInt, Field(ge=1, le=_MAX_EPOCHS)]
    losses: ResidualEpochLosses


class ResidualTrainingRecord(BaseModel):
    """Strict scalar/reference-only form of `residual-training-record/v1`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["residual-training-record/v1"]
    model: ResidualStateSpaceConfig
    training: ResidualTrainingSettings
    run: ResidualTrainingRun
    dataset: ResidualDatasetReference
    artifact: ResidualArtifactRecord
    epochs: tuple[ResidualEpochRecord, ...] = Field(max_length=_MAX_EPOCHS)

    @model_validator(mode="after")
    def validate_epoch_history(self) -> Self:
        if len(self.epochs) != self.training.epochs:
            raise ValueError("training record epoch history is incomplete")
        if tuple(epoch.epoch for epoch in self.epochs) != tuple(
            range(1, self.training.epochs + 1)
        ):
            raise ValueError("training record epoch numbering mismatch")
        return self


class MotionModelComponents(BaseModel):
    """Offline artifacts and fully resolved configs included in one package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    weights: Path
    residual_config: ResidualStateSpaceConfig
    face_event_config: FaceEventConfig
    head_gesture_config: HeadGestureConfig
    controller_response_config: ControllerResponseConfig
    anchor_config: ProceduralMotionConfig


class MotionModelMetadata(BaseModel):
    """Research and runtime metadata retained beside package components."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    identities: PackageIdentities
    training_record: ResidualTrainingRecord
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
    training_record: ResidualTrainingRecord
    metrics_reference: str
    seed_policy: SeedPolicy
    candidate_composer: ProductionCandidateComposer


def save_package(
    path: str | Path,
    components: MotionModelComponents,
    metadata: MotionModelMetadata,
) -> MotionModelPackage:
    """Save an allowlisted package after validating identities and provenance."""

    package_path = Path(path)
    if package_path.exists():
        raise FileExistsError(f"model package already exists: {package_path}")
    components = _revalidate_components(components)
    weights_bytes = _read_regular_bytes(components.weights)
    weights = load(weights_bytes)
    _validate_finite_weights(weights)
    weights_sha256 = _sha256_bytes(weights_bytes)
    _validate_components(components, metadata.identities)
    training_record = _validate_training_record(
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
        artifact_bytes = {
            _CONFIG_PATHS["anchor"]: _encode_json(
                components.anchor_config.model_dump(mode="json")
            ),
            _CONFIG_PATHS["residual"]: _encode_json(
                components.residual_config.model_dump(mode="json")
            ),
            _CONFIG_PATHS["face_events"]: _encode_json(
                components.face_event_config.model_dump(mode="json")
            ),
            _CONFIG_PATHS["head_gestures"]: _encode_json(
                components.head_gesture_config.model_dump(mode="json")
            ),
            _CONFIG_PATHS["controller_response"]: _encode_json(
                components.controller_response_config.model_dump(mode="json")
            ),
            _TRAINING_RECORD_PATH: _encode_json(
                training_record.model_dump(mode="json")
            ),
            _WEIGHTS_PATH: weights_bytes,
        }
        for relative_path, payload in artifact_bytes.items():
            destination = staged / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)

        checksums = {
            relative_path: _sha256_bytes(artifact_bytes[relative_path])
            for relative_path in sorted(artifact_bytes)
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
        (staged / _MANIFEST_PATH).write_bytes(
            _encode_json(manifest.model_dump(mode="json"))
        )
        staged.replace(package_path)

    return _package_paths(package_path)


def load_package(
    path: str | Path,
    expected_identities: PackageIdentities | Mapping[str, object],
) -> LoadedMotionModel:
    """Validate a package completely, then construct software model components."""

    package = _package_paths(Path(path))
    expected = PackageIdentities.model_validate(expected_identities)
    _validate_package_inventory(package.path)
    manifest = _load_manifest(_read_regular_bytes(package.manifest))
    _validate_expected_identities(manifest.identities, expected)
    snapshot = _snapshot_artifacts(package.path, manifest.checksums)

    residual_config = ResidualStateSpaceConfig.model_validate(
        _decode_json(snapshot[_CONFIG_PATHS["residual"]], label="residual config")
    )
    anchor_config = ProceduralMotionConfig.model_validate(
        _decode_json(snapshot[_CONFIG_PATHS["anchor"]], label="anchor config")
    )
    face_event_config = FaceEventConfig.model_validate(
        _decode_json(snapshot[_CONFIG_PATHS["face_events"]], label="face-event config")
    )
    head_gesture_config = HeadGestureConfig.model_validate(
        _decode_json(
            snapshot[_CONFIG_PATHS["head_gestures"]], label="head-gesture config"
        )
    )
    controller_config = ControllerResponseConfig.model_validate(
        _decode_json(
            snapshot[_CONFIG_PATHS["controller_response"]],
            label="controller-response config",
        )
    )
    components = MotionModelComponents(
        weights=package.weights,
        residual_config=residual_config,
        face_event_config=face_event_config,
        head_gesture_config=head_gesture_config,
        controller_response_config=controller_config,
        anchor_config=anchor_config,
    )
    _validate_components(components, manifest.identities)

    training_record = _validate_training_record(
        _decode_json(snapshot[_TRAINING_RECORD_PATH], label="training record"),
        residual_config=residual_config,
        weights_sha256=manifest.checksums[_WEIGHTS_PATH],
        seed_policy=manifest.seed_policy,
    )
    weights = load(snapshot[_WEIGHTS_PATH])
    _validate_finite_weights(weights)

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
    candidate_composer = ProductionCandidateComposer(
        anchor_planner=AnchorPlanner(config=anchor_config),
        residual_model=residual_model,
        face_events=face_event_generator,
        head_scheduler=head_gesture_scheduler,
        controller_response=controller_response,
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
        candidate_composer=candidate_composer,
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
        "affect schema",
        components.anchor_config.affect_schema_id,
        identities.affect_schema_id,
    )
    _require_identity(
        "calibration",
        components.anchor_config.calibration_sha256,
        identities.calibration_sha256,
    )
    _require_identity(
        "controller settings",
        components.anchor_config.controller_settings_sha256,
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
    if (
        components.controller_response_config.controller_settings_sha256
        != identities.controller_settings_sha256
    ):
        raise ValueError("embedded controller settings identity mismatch")
    response_actuators = tuple(
        actuator.actuator_name
        for actuator in components.controller_response_config.actuators
    )
    if components.anchor_config.semantic_actuator_names != response_actuators:
        raise ValueError("anchor and controller-response actuator identities mismatch")
    if components.residual_config.actuator_names != response_actuators:
        raise ValueError(
            "residual and controller-response actuator identities mismatch"
        )

    affect_dimensions = components.residual_config.affect_dimensions
    if components.face_event_config.affect_dimensions != affect_dimensions:
        raise ValueError("face-event and residual affect dimensions mismatch")
    if components.head_gesture_config.affect_dimensions != affect_dimensions:
        raise ValueError("head-gesture and residual affect dimensions mismatch")

    head_names = components.head_gesture_config.semantics.actuator_names
    if head_names != response_actuators[: len(head_names)]:
        raise ValueError(
            "head semantic actuator identities/order mismatch controller response"
        )
    recovery_names = tuple(
        target.actuator_name
        for target in components.head_gesture_config.recovery_targets
    )
    if recovery_names != head_names:
        raise ValueError("head recovery actuator identities/order mismatch")
    for head_policy in components.head_gesture_config.gestures:
        expected_name = components.head_gesture_config.semantics.actuator_for(
            head_policy.kind
        )
        if head_policy.actuator_name != expected_name:
            raise ValueError("head policy actuator reference mismatch")

    face_names = tuple(
        name
        for policy in components.face_event_config.events
        for name in policy.actuator_names
    )
    if len(face_names) != len(set(face_names)):
        raise ValueError("face-event actuator references must be unique")
    if set(face_names) & set(head_names):
        raise ValueError("face-event actuator references overlap head axes")
    if not _is_ordered_subsequence(face_names, response_actuators):
        raise ValueError(
            "face-event actuator identities/order mismatch controller response"
        )

    response_by_name = {
        parameters.actuator_name: parameters
        for parameters in components.controller_response_config.actuators
    }
    for face_policy in components.face_event_config.events:
        _validate_face_response(face_policy, response_by_name)
    for head_policy in components.head_gesture_config.gestures:
        _validate_head_response(head_policy, response_by_name)


def _revalidate_components(components: MotionModelComponents) -> MotionModelComponents:
    """Re-run nested config validators even for unchecked `model_copy` values."""

    try:
        return MotionModelComponents(
            weights=components.weights,
            residual_config=ResidualStateSpaceConfig.model_validate(
                components.residual_config.model_dump(mode="python")
            ),
            face_event_config=FaceEventConfig.model_validate(
                components.face_event_config.model_dump(mode="python")
            ),
            head_gesture_config=HeadGestureConfig.model_validate(
                components.head_gesture_config.model_dump(mode="python")
            ),
            controller_response_config=ControllerResponseConfig.model_validate(
                components.controller_response_config.model_dump(mode="python")
            ),
            anchor_config=ProceduralMotionConfig.model_validate(
                components.anchor_config.model_dump(mode="python")
            ),
        )
    except ValidationError as error:
        raise ValueError("resolved component config is invalid") from error


def _is_ordered_subsequence(
    names: tuple[str, ...],
    ordered_universe: tuple[str, ...],
) -> bool:
    positions = {name: index for index, name in enumerate(ordered_universe)}
    try:
        indices = tuple(positions[name] for name in names)
    except KeyError:
        return False
    return indices == tuple(sorted(indices))


def _validate_face_response(
    policy: FaceEventPolicy,
    response_by_name: Mapping[str, ActuatorResponseParameters],
) -> None:
    try:
        minimum_s = max(
            _face_transition_seconds(
                policy.amplitude_max,
                response_by_name[actuator_name],
            )
            for actuator_name in policy.actuator_names
        )
    except KeyError as error:
        raise ValueError("face-event actuator reference is unknown") from error
    if policy.onset_s < minimum_s or policy.release_s < minimum_s:
        raise ValueError(
            f"{policy.kind.value} phase is shorter than controller response"
        )


def _validate_head_response(
    policy: HeadGesturePolicy,
    response_by_name: Mapping[str, ActuatorResponseParameters],
) -> None:
    try:
        parameters = response_by_name[policy.actuator_name]
    except KeyError as error:
        raise ValueError("head policy actuator reference is unknown") from error
    maximum_amplitude = policy.amplitude.maximum
    if policy.kind.value in {"nod", "shake"}:
        available_s = policy.duration_s.minimum / (2 * policy.cycles.maximum)
        maximum_distance = 2.0 * maximum_amplitude
    else:
        available_s = policy.duration_s.minimum
        maximum_distance = maximum_amplitude
    if available_s < _head_transition_seconds(maximum_distance, parameters):
        raise ValueError(
            f"{policy.kind.value} duration bounds are shorter than controller response"
        )
    if policy.recovery_s.minimum < _head_transition_seconds(
        maximum_amplitude, parameters
    ):
        raise ValueError(
            f"{policy.kind.value} recovery bounds are shorter than controller response"
        )


def _face_transition_seconds(
    distance: float,
    parameters: ActuatorResponseParameters,
) -> float:
    acceleration = parameters.max_acceleration_per_s2
    max_speed = parameters.max_velocity_per_s
    triangular_limit = max_speed**2 / acceleration
    if distance <= triangular_limit:
        return 2.0 * math.sqrt(distance / acceleration)
    return distance / max_speed + max_speed / acceleration


def _head_transition_seconds(
    distance: float,
    parameters: ActuatorResponseParameters,
) -> float:
    return max(
        _HEAD_PEAK_VELOCITY * distance / parameters.max_velocity_per_s,
        math.sqrt(
            _HEAD_PEAK_ACCELERATION * distance / parameters.max_acceleration_per_s2
        ),
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
    record: ResidualTrainingRecord | Mapping[str, object],
    *,
    residual_config: ResidualStateSpaceConfig,
    weights_sha256: str,
    seed_policy: SeedPolicy,
) -> ResidualTrainingRecord:
    source: object = (
        record.model_dump(mode="python")
        if isinstance(record, ResidualTrainingRecord)
        else record
    )
    try:
        validated = ResidualTrainingRecord.model_validate(source)
    except ValidationError as error:
        raise ValueError(
            f"training record contains invalid, missing, or undeclared fields: {error}"
        ) from error
    if validated.model != residual_config:
        raise ValueError("training record model config identity mismatch")
    if validated.run.seed != seed_policy.training_seed:
        raise ValueError("training record seed and package seed policy mismatch")
    if (
        validated.training.controller_settings_sha256
        != residual_config.controller_settings_sha256
    ):
        raise ValueError("training controller settings identity mismatch")
    if validated.artifact.weights_sha256 != weights_sha256:
        raise ValueError("training record weights checksum mismatch")
    return validated


def _load_manifest(data: bytes) -> _PackageManifest:
    try:
        return _PackageManifest.model_validate(_decode_json(data, label="manifest"))
    except (ValidationError, ValueError) as error:
        raise ValueError("model package manifest is unreadable") from error


def _validate_package_inventory(package_path: Path) -> None:
    try:
        root_mode = package_path.lstat().st_mode
    except OSError as error:
        raise ValueError("model package path is unreadable") from error
    if not stat.S_ISDIR(root_mode) or stat.S_ISLNK(root_mode):
        raise ValueError("model package path must be a real directory")
    actual_files: set[str] = set()
    for path in package_path.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        relative = path.relative_to(package_path).as_posix()
        if not stat.S_ISREG(mode):
            raise ValueError(f"model package contains non-regular entry: {relative}")
        actual_files.add(relative)
    expected_files = {*_DECLARED_ARTIFACT_PATHS, _MANIFEST_PATH}
    if actual_files != expected_files:
        raise ValueError("model package contains undeclared artifacts")


def _snapshot_artifacts(
    package_path: Path,
    checksums: Mapping[str, str],
) -> Mapping[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for relative_path in sorted(_DECLARED_ARTIFACT_PATHS):
        payload = _read_regular_bytes(package_path / relative_path)
        if _sha256_bytes(payload) != checksums[relative_path]:
            raise ValueError(f"artifact checksum mismatch: {relative_path}")
        snapshot[relative_path] = payload
    return MappingProxyType(snapshot)


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


def _decode_json(data: bytes, *, label: str) -> dict[str, object]:
    try:
        document = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return document


def _encode_json(document: object) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_regular_bytes(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError(f"package artifact is unreadable: {path.name}") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"package artifact is not a regular file: {path.name}")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_finite_weights(weights: Mapping[str, object]) -> None:
    """Reject NaN/Inf tensors before constructing a deployable model."""

    import torch

    if not weights or any(
        not isinstance(tensor, torch.Tensor) or not bool(torch.isfinite(tensor).all())
        for tensor in weights.values()
    ):
        raise ValueError("model weights must contain only finite tensors")
