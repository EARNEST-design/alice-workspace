"""Coupled controls that must not expose unsafe independent motor degrees."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from alice.contracts.actuation import ActuatorTarget, NormalizedPosition


class HorizontalGaze(BaseModel):
    """One horizontal gaze coordinate shared by Alice's two eyes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position: NormalizedPosition


class EyelidAperture(BaseModel):
    """Shared eyelid aperture: ``-1`` closed, ``0`` Home, ``+1`` open."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    position: NormalizedPosition


def expand_horizontal_gaze(gaze: HorizontalGaze) -> tuple[ActuatorTarget, ...]:
    """Expand gaze atomically; both eye motors always receive the same direction."""

    return (
        ActuatorTarget(
            actuator_name="right_eye_horizontal",
            normalized_position=gaze.position,
        ),
        ActuatorTarget(
            actuator_name="left_eye_horizontal",
            normalized_position=gaze.position,
        ),
    )


def expand_eyelid_aperture(
    aperture: EyelidAperture,
) -> tuple[ActuatorTarget, ...]:
    """Expand aperture atomically so upper and lower eyelids stay coordinated."""

    return (
        ActuatorTarget(
            actuator_name="lower_eyelids",
            normalized_position=aperture.position,
        ),
        ActuatorTarget(
            actuator_name="upper_eyelids",
            normalized_position=aperture.position,
        ),
    )
