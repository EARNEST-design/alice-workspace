from __future__ import annotations

import pytest

from alice.control.virtual_actuators import (
    EyelidAperture,
    HorizontalGaze,
    expand_eyelid_aperture,
    expand_horizontal_gaze,
)


def test_horizontal_gaze_expands_to_both_eyes_in_lockstep() -> None:
    targets = expand_horizontal_gaze(HorizontalGaze(position=0.65))

    assert [target.actuator_name for target in targets] == [
        "right_eye_horizontal",
        "left_eye_horizontal",
    ]
    assert {target.normalized_position for target in targets} == {0.65}


def test_eyelid_aperture_expands_to_both_eyelid_motors_in_lockstep() -> None:
    targets = expand_eyelid_aperture(EyelidAperture(position=-0.8))

    assert [target.actuator_name for target in targets] == [
        "lower_eyelids",
        "upper_eyelids",
    ]
    assert {target.normalized_position for target in targets} == {-0.8}


@pytest.mark.parametrize("position", [-1.01, 1.01, float("nan")])
def test_horizontal_gaze_rejects_out_of_range_or_nonfinite_values(
    position: float,
) -> None:
    with pytest.raises(ValueError):
        HorizontalGaze(position=position)


@pytest.mark.parametrize("position", [-1.01, 1.01, float("nan")])
def test_eyelid_aperture_rejects_out_of_range_or_nonfinite_values(
    position: float,
) -> None:
    with pytest.raises(ValueError):
        EyelidAperture(position=position)
