"""Target coalescing for pending hardware-independent motion proposals."""

from __future__ import annotations

import math
from collections.abc import Iterable

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate


def coalesce_updates(
    updates: Iterable[TargetUpdate],
    transmit_at_s: float,
) -> tuple[TargetUpdate, ...]:
    """Merge due targets per actuator while retaining future event timing."""

    if not math.isfinite(transmit_at_s) or transmit_at_s < 0.0:
        raise ValueError("transmit_at_s must be finite and non-negative")
    pending = tuple(updates)
    offsets = [update.offset_s for update in pending]
    if any(current <= previous for previous, current in zip(offsets, offsets[1:])):
        raise ValueError("target update offsets must be strictly increasing")

    latest_due: dict[str, ActuatorTarget] = {}
    future_start = 0
    for future_start, update in enumerate(pending):
        if update.offset_s > transmit_at_s:
            break
        for target in update.targets:
            latest_due[target.actuator_name] = target
    else:
        future_start = len(pending)

    future = pending[future_start:]
    if not latest_due:
        return future
    coalesced = TargetUpdate(
        offset_s=transmit_at_s,
        targets=tuple(latest_due[name] for name in sorted(latest_due)),
    )
    return (coalesced, *future)
