"""Overlapping-horizon acceptance for hardware-independent motion proposals."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Literal, Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.affect import MonotonicNanoseconds
from alice.contracts.blendshapes import NonEmptyString
from alice.contracts.motion import MotionProposal, TargetUpdate
from alice.motion.intent_filter import FilteredIntent
from alice.motion.state import GeneratorState


class _CandidateGenerator(Protocol):
    def step(
        self,
        intent: FilteredIntent,
        state: TargetUpdate,
        seed: int,
        horizon_s: float,
        *,
        generated_monotonic_ns: int,
    ) -> MotionProposal: ...


class AcceptedPrefix(BaseModel):
    """The bounded prefix accepted from one longer motion proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["accepted-prefix/v1"]
    proposal_id: NonEmptyString
    starts_at_ns: MonotonicNanoseconds
    ends_at_ns: MonotonicNanoseconds
    updates: tuple[TargetUpdate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_window(self) -> AcceptedPrefix:
        if self.ends_at_ns <= self.starts_at_ns:
            raise ValueError("accepted prefix must have positive duration")
        if self.updates[0].offset_s != 0.0:
            raise ValueError("accepted prefix must begin at offset zero")
        duration_s = (self.ends_at_ns - self.starts_at_ns) / 1_000_000_000
        if any(update.offset_s > duration_s for update in self.updates):
            raise ValueError("accepted prefix update exceeds its time window")
        return self


class StreamingMotionGenerator:
    """Generate full candidates while accepting only a short continuous prefix."""

    def __init__(
        self,
        *,
        generator: _CandidateGenerator,
        horizon_s: float,
        prefix_duration_s: float,
    ) -> None:
        if not math.isfinite(horizon_s) or horizon_s <= 0.0:
            raise ValueError("horizon_s must be finite and positive")
        if not math.isfinite(prefix_duration_s) or prefix_duration_s <= 0.0:
            raise ValueError("prefix_duration_s must be finite and positive")
        if prefix_duration_s > horizon_s:
            raise ValueError("prefix duration must not exceed candidate horizon")
        prefix_duration_ns = round(prefix_duration_s * 1_000_000_000)
        if prefix_duration_ns <= 0:
            raise ValueError("prefix duration must cover at least one nanosecond")

        self._generator = generator
        self._horizon_s = horizon_s
        self._prefix_duration_s = prefix_duration_s
        self._prefix_duration_ns = prefix_duration_ns

    def replan(
        self,
        intent: FilteredIntent,
        state: GeneratorState,
        now_ns: int,
    ) -> tuple[AcceptedPrefix, GeneratorState]:
        """Accept a continuous prefix and return its complete continuation state."""

        if now_ns < state.monotonic_ns:
            raise ValueError("now_ns must not precede persisted generator state")
        if intent.accepted_monotonic_ns > now_ns:
            raise ValueError("filtered intent postdates replan time")

        rng = self._restore_numpy_rng(state.numpy_rng_state)
        seed = int(rng.integers(0, np.iinfo(np.int64).max))
        proposal = self._generator.step(
            intent,
            state.last_accepted_target,
            seed,
            self._horizon_s,
            generated_monotonic_ns=now_ns,
        )
        self._validate_candidate(proposal, state=state, now_ns=now_ns)

        updates = tuple(
            update
            for update in proposal.horizon.updates
            if update.offset_s <= self._prefix_duration_s
        )
        if not updates:
            raise ValueError("candidate has no updates inside the accepted prefix")

        ends_at_ns = now_ns + self._prefix_duration_ns
        prefix = AcceptedPrefix(
            schema_version="accepted-prefix/v1",
            proposal_id=proposal.proposal_id,
            starts_at_ns=now_ns,
            ends_at_ns=ends_at_ns,
            updates=updates,
        )
        boundary_target = updates[-1].model_copy(update={"offset_s": 0.0})
        state_values = state.model_dump()
        state_values.update(
            {
                "last_accepted_target": boundary_target,
                "filtered_intent": intent,
                "numpy_rng_state": rng.bit_generator.state,
                "monotonic_ns": ends_at_ns,
            }
        )
        return prefix, GeneratorState.model_validate(state_values)

    @staticmethod
    def _restore_numpy_rng(state: Mapping[str, object]) -> np.random.Generator:
        bit_generator_name = state.get("bit_generator")
        bit_generators: dict[str, type[np.random.BitGenerator]] = {
            "MT19937": np.random.MT19937,
            "PCG64": np.random.PCG64,
            "PCG64DXSM": np.random.PCG64DXSM,
            "Philox": np.random.Philox,
            "SFC64": np.random.SFC64,
        }
        if not isinstance(bit_generator_name, str):
            raise ValueError("NumPy RNG state is missing its bit-generator identity")
        bit_generator_type = bit_generators.get(bit_generator_name)
        if bit_generator_type is None:
            raise ValueError(f"unsupported NumPy bit generator: {bit_generator_name!r}")
        bit_generator = bit_generator_type()
        try:
            bit_generator.state = copy.deepcopy(dict(state))
        except (TypeError, ValueError) as error:
            raise ValueError("invalid NumPy RNG state") from error
        return np.random.Generator(bit_generator)

    def _validate_candidate(
        self,
        proposal: MotionProposal,
        *,
        state: GeneratorState,
        now_ns: int,
    ) -> None:
        if proposal.generated_monotonic_ns != now_ns:
            raise ValueError("candidate generation time does not match replan time")
        if proposal.expires_monotonic_ns <= now_ns + self._prefix_duration_ns:
            raise ValueError("candidate expires before the accepted prefix ends")
        if proposal.horizon.updates[-1].offset_s < self._prefix_duration_s:
            raise ValueError("candidate horizon does not cover accepted prefix")
        identities = (
            ("model identity", proposal.model_id, state.model_id),
            ("model identity", proposal.model_sha256, state.model_sha256),
            (
                "calibration identity",
                proposal.calibration_sha256,
                state.calibration_sha256,
            ),
            (
                "controller identity",
                proposal.controller_settings_sha256,
                state.controller_settings_sha256,
            ),
        )
        for label, candidate_value, state_value in identities:
            if candidate_value != state_value:
                raise ValueError(f"candidate {label} changed")

        first = proposal.horizon.updates[0]
        if first.offset_s != 0.0 or StreamingMotionGenerator._positions(
            first
        ) != StreamingMotionGenerator._positions(state.last_accepted_target):
            raise ValueError("discontinuous candidate start")

    @staticmethod
    def _positions(update: TargetUpdate) -> dict[str, float]:
        return {
            target.actuator_name: target.normalized_position
            for target in update.targets
        }
