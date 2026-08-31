"""Fail-closed loading and validation of semantic hardware manifests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.actuation import PoseRequest
from alice.contracts.blendshapes import NonEmptyString, Sha256Hex

ChannelNumber = Annotated[int, Field(ge=0, le=23)]
QuarterMicroseconds = Annotated[int, Field(gt=0)]


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: NonEmptyString
    detail: NonEmptyString


class ControllerIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["pololu-maestro"]
    serial_number: NonEmptyString


class PreflightRequirement(BaseModel):
    """A physical fact which must be established afresh before actuation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_id: NonEmptyString
    description: NonEmptyString
    satisfied: Literal[False]
    evidence: tuple[EvidenceReference, ...]


class ActuatorDefinition(BaseModel):
    """A semantic actuator identity and its reviewed software envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonEmptyString
    channel: ChannelNumber
    function: NonEmptyString
    software_min_qus: QuarterMicroseconds
    home_qus: QuarterMicroseconds
    software_max_qus: QuarterMicroseconds
    firmware_min_qus: QuarterMicroseconds
    firmware_max_qus: QuarterMicroseconds
    firmware_speed: Annotated[int, Field(ge=0)]
    firmware_acceleration: Annotated[int, Field(ge=0)]
    decreasing_effect: NonEmptyString | None = None
    increasing_effect: NonEmptyString | None = None
    inspection_required: bool = False
    preflight_requirement_ids: tuple[NonEmptyString, ...] = ()
    evidence: tuple[EvidenceReference, ...]

    @model_validator(mode="after")
    def validate_limits(self) -> ActuatorDefinition:
        if not (
            self.firmware_min_qus
            <= self.software_min_qus
            <= self.home_qus
            <= self.software_max_qus
            <= self.firmware_max_qus
        ):
            raise ValueError(
                "actuator limits must satisfy firmware_min <= software_min <= "
                "home <= software_max <= firmware_max"
            )
        if not self.evidence:
            raise ValueError("actuator requires at least one evidence reference")
        return self

    def target_qus(self, normalized_position: float) -> int:
        """Map ``[-1, 1]`` piecewise-linearly around the reviewed Home value."""

        if not -1.0 <= normalized_position <= 1.0:
            raise ValueError("normalized position must be in [-1, 1]")
        if normalized_position < 0.0:
            span = self.home_qus - self.software_min_qus
        else:
            span = self.software_max_qus - self.home_qus
        return round(self.home_qus + normalized_position * span)


class HardwareManifest(BaseModel):
    """Versioned semantic mapping used to bind commands to calibration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["hardware-manifest/v1"]
    hardware_id: NonEmptyString
    controller: ControllerIdentity
    actuators: tuple[ActuatorDefinition, ...]
    preflight_requirements: tuple[PreflightRequirement, ...]
    evidence: tuple[EvidenceReference, ...]

    @model_validator(mode="after")
    def validate_manifest(self) -> HardwareManifest:
        names = [actuator.name for actuator in self.actuators]
        channels = [actuator.channel for actuator in self.actuators]
        requirement_ids = [item.requirement_id for item in self.preflight_requirements]
        if len(names) != len(set(names)):
            raise ValueError("actuator names must be unique")
        if len(channels) != len(set(channels)):
            raise ValueError("actuator channels must be unique")
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("preflight requirement IDs must be unique")
        known_requirements = set(requirement_ids)
        for actuator in self.actuators:
            unknown = set(actuator.preflight_requirement_ids) - known_requirements
            if unknown:
                raise ValueError(
                    f"actuator {actuator.name!r} references unknown preflight "
                    f"requirements: {sorted(unknown)}"
                )
            if actuator.inspection_required and not actuator.preflight_requirement_ids:
                raise ValueError(
                    f"actuator {actuator.name!r} requires inspection but has no "
                    "preflight requirement"
                )
        return self

    @property
    def calibration_sha256(self) -> Sha256Hex:
        """Hash all fields which define semantic target-to-channel calibration."""

        calibration = {
            "schema_version": self.schema_version,
            "hardware_id": self.hardware_id,
            "controller": {
                "kind": self.controller.kind,
                "serial_number": self.controller.serial_number,
            },
            "actuators": [
                {
                    "channel": actuator.channel,
                    "decreasing_effect": actuator.decreasing_effect,
                    "firmware_acceleration": actuator.firmware_acceleration,
                    "firmware_max_qus": actuator.firmware_max_qus,
                    "firmware_min_qus": actuator.firmware_min_qus,
                    "firmware_speed": actuator.firmware_speed,
                    "function": actuator.function,
                    "home_qus": actuator.home_qus,
                    "increasing_effect": actuator.increasing_effect,
                    "inspection_required": actuator.inspection_required,
                    "name": actuator.name,
                    "preflight_requirement_ids": actuator.preflight_requirement_ids,
                    "software_max_qus": actuator.software_max_qus,
                    "software_min_qus": actuator.software_min_qus,
                }
                for actuator in sorted(self.actuators, key=lambda item: item.name)
            ],
        }
        payload = json.dumps(
            calibration, sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(payload).hexdigest()

    @property
    def unmet_preflight_requirements(self) -> tuple[PreflightRequirement, ...]:
        return tuple(item for item in self.preflight_requirements if not item.satisfied)

    def actuator(self, name: str) -> ActuatorDefinition:
        for actuator in self.actuators:
            if actuator.name == name:
                return actuator
        raise ValueError(f"unknown actuator: {name!r}")

    def validate_request(self, request: PoseRequest, *, now_monotonic_ns: int) -> None:
        """Reject a request not bound to this manifest or no longer fresh."""

        if request.hardware_id != self.hardware_id:
            raise ValueError("hardware_id mismatch")
        if request.calibration_sha256 != self.calibration_sha256:
            raise ValueError("calibration_sha256 mismatch")
        if request.issued_monotonic_ns > now_monotonic_ns:
            raise ValueError("pose request was issued in the future")
        if request.is_expired(now_monotonic_ns=now_monotonic_ns):
            raise ValueError("pose request expired")
        for target in request.targets:
            self.actuator(target.actuator_name)


def load_manifest(path: str | Path) -> HardwareManifest:
    """Load and fully validate a YAML hardware manifest without touching hardware."""

    manifest_path = Path(path)
    with manifest_path.open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise ValueError("hardware manifest root must be a mapping")
    return HardwareManifest.model_validate(document)
