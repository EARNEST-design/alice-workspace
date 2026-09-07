"""Behavioral tests for sparse target-update coalescing."""

from __future__ import annotations

import math

import pytest

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.motion.coalescing import coalesce_updates


def _update(offset_s: float, **targets: float) -> TargetUpdate:
    return TargetUpdate(
        offset_s=offset_s,
        targets=tuple(
            ActuatorTarget(
                actuator_name=actuator_name,
                normalized_position=position,
            )
            for actuator_name, position in targets.items()
        ),
    )


def _target(update: TargetUpdate, actuator_name: str) -> float:
    return next(
        target.normalized_position
        for target in update.targets
        if target.actuator_name == actuator_name
    )


def test_coalescing_keeps_latest_unsent_target() -> None:
    """Keeping the first pending value would transmit a superseded target."""

    result = coalesce_updates(
        (
            _update(0.01, neck_rotation=0.01),
            _update(0.02, neck_rotation=0.02),
            _update(0.03, neck_rotation=0.03),
        ),
        transmit_at_s=0.04,
    )

    assert len(result) == 1
    assert result[0].offset_s == 0.04
    assert _target(result[0], "neck_rotation") == 0.03


def test_coalescing_retains_latest_value_for_each_sparse_actuator() -> None:
    """Replacing whole sparse packets could erase another actuator's target."""

    result = coalesce_updates(
        (
            _update(0.01, neck_rotation=0.1, mouth_open=0.2),
            _update(0.02, mouth_open=0.4),
            _update(0.03, head_tilt=-0.3),
        ),
        transmit_at_s=0.03,
    )

    assert len(result) == 1
    assert {
        target.actuator_name: target.normalized_position
        for target in result[0].targets
    } == {
        "head_tilt": -0.3,
        "mouth_open": 0.4,
        "neck_rotation": 0.1,
    }


def test_coalescing_preserves_distinct_future_event_timestamps() -> None:
    """Flattening the full horizon would erase timing of unsent future events."""

    future_mouth = _update(0.06, mouth_open=0.7)
    future_eyes = _update(0.08, lower_eyelids=-0.4)

    result = coalesce_updates(
        (
            _update(0.01, neck_rotation=0.1),
            _update(0.03, neck_rotation=0.2),
            future_mouth,
            future_eyes,
        ),
        transmit_at_s=0.04,
    )

    assert [update.offset_s for update in result] == [0.04, 0.06, 0.08]
    assert result[1:] == (future_mouth, future_eyes)


def test_update_at_transmission_instant_is_due() -> None:
    """Treating an exact-boundary target as future would delay its transmission."""

    result = coalesce_updates(
        (_update(0.02, neck_rotation=0.1), _update(0.04, neck_rotation=0.5)),
        transmit_at_s=0.04,
    )

    assert len(result) == 1
    assert _target(result[0], "neck_rotation") == 0.5


def test_no_due_updates_preserves_future_schedule() -> None:
    """Coalescing with no due work must not rewrite future event times."""

    future = (_update(0.05, neck_rotation=0.1), _update(0.07, mouth_open=0.2))

    assert coalesce_updates(future, transmit_at_s=0.04) == future


@pytest.mark.parametrize("transmit_at_s", [-0.1, math.inf, math.nan])
def test_coalescing_rejects_invalid_transmission_time(transmit_at_s: float) -> None:
    """An invalid transmission instant makes due/future ordering undefined."""

    with pytest.raises(ValueError, match="transmit_at_s"):
        coalesce_updates((_update(0.01, neck_rotation=0.1),), transmit_at_s)


def test_coalescing_rejects_non_increasing_update_offsets() -> None:
    """Out-of-order input could select an older target as the latest one."""

    with pytest.raises(ValueError, match="strictly increasing"):
        coalesce_updates(
            (_update(0.02, neck_rotation=0.2), _update(0.01, neck_rotation=0.1)),
            transmit_at_s=0.03,
        )
