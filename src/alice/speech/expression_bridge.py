"""Persistent motion on the audible sample clock, with explicit source identity."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import torch
import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field
from safetensors.torch import save

from alice.contracts.affect import AffectIntent, AffectVectorSchema
from alice.contracts.motion import TargetUpdate
from alice.contracts.speech import SpeechSyncConfig
from alice.models.head_scheduler import HeadGestureScheduler, load_head_gesture_config
from alice.models.residual_state_space import (
    ResidualStateSpace,
    load_residual_state_space_config,
)
from alice.motion.anchors import AnchorPlanner, load_procedural_motion_config
from alice.motion.controller_response import (
    ControllerResponse,
    load_controller_response_config,
)
from alice.motion.face_events import FaceEventGenerator, load_face_event_config
from alice.motion.intent_filter import (
    FilteredIntent,
    IntentFilter,
    IntentFilterConfig,
    SupportStatus,
)
from alice.motion.state import ActuatorVelocity, EventHistoryRecord, GeneratorState
from alice.motion.streaming import (
    AcceptedPrefix,
    ProductionCandidateComposer,
    StreamingMotionGenerator,
)
from alice.speech.timeline import SpeechFrame


class AuthoredExpressionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["authored-expression/v1"]
    valence_threshold: float = Field(gt=0, lt=1, allow_inf_nan=False)
    anchor_scale: float = Field(gt=0, le=1, allow_inf_nan=False)
    transition_s: float = Field(ge=0.8, le=5, allow_inf_nan=False)
    positive_anchor: str
    negative_anchor: str
    provenance: str = Field(min_length=1)


class _AuthoredAnchorPlanner(AnchorPlanner):
    policy: AuthoredExpressionConfig

    def _interpolated_target(self, intent: FilteredIntent) -> dict[str, float]:
        # This policy deliberately does not populate AffectAnchorMapping or the
        # learned IntentFilter support set. These labels are authored choices.
        name = "neutral"
        if intent.vector[0] > self.policy.valence_threshold:
            name = self.policy.positive_anchor
        elif intent.vector[0] < -self.policy.valence_threshold:
            name = self.policy.negative_anchor
        return self._scaled_anchor(name, intent.intensity * self.policy.anchor_scale)


class _DisabledHeadScheduler(HeadGestureScheduler):
    def decision_due(
        self, history: tuple[EventHistoryRecord, ...], *, generated_monotonic_ns: int
    ) -> bool:
        return False


class _Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["speech-expression-state/v1"] = "speech-expression-state/v1"
    identity_sha256: str
    state: GeneratorState
    prefix: AcceptedPrefix | None
    boundary: GeneratorState | None
    last_frame: SpeechFrame | None
    last_sample: int
    sample_rate: int | None
    cancelled: bool


class ExpressionBridge:
    """Keep speculative boundaries separate from state reached by the DAC.

    Runtime prefixes remain 400 ms at the qualified 5 Hz motion cadence. A new
    audible cue is used at the next replan (up to 400 ms later), never applied
    early to finish a previously accepted prefix. Model work belongs on a
    dedicated consumer thread, not in an audio callback or event loop.
    """

    def __init__(
        self,
        *,
        config_root: Path,
        seed: int,
        generation_id: str,
        mode: Literal["authored", "learned-fallback"] = "learned-fallback",
        origin_ns: int = 0,
        head_enabled: bool = False,
    ) -> None:
        if mode not in {"authored", "learned-fallback"} or not 0 <= seed < 2**32:
            raise ValueError("invalid expression mode or seed")
        if origin_ns < 0 or not generation_id:
            raise ValueError("invalid generation origin or identity")
        self.mode, self.origin_ns = mode, origin_ns
        models = config_root / "models"
        anchor = load_procedural_motion_config(models / "procedural-motion-v1.yaml")
        response = load_controller_response_config(models / "maestro-response-v1.yaml")
        face = load_face_event_config(models / "face-events-v1.yaml")
        head = load_head_gesture_config(models / "head-gestures-v1.yaml")
        residual_config = load_residual_state_space_config(
            models / "residual-state-space-v1.yaml"
        )
        policy = AuthoredExpressionConfig.model_validate_json(
            (config_root / "speech/authored-expression-v1.json").read_text()
        )
        self.sync_config = SpeechSyncConfig.model_validate_json(
            (config_root / "speech/sync-hardware-v1.json").read_text()
        )
        anchor_planner: AnchorPlanner
        if mode == "authored":
            anchor.anchor(policy.positive_anchor)
            anchor.anchor(policy.negative_anchor)
            anchor = anchor.model_copy(
                update={"anchor_transition_s": policy.transition_s}
            )
            authored = _AuthoredAnchorPlanner(config=anchor)
            authored.policy = policy
            anchor_planner = authored
        else:
            anchor_planner = AnchorPlanner(config=anchor)
        affect = config_root / "affect"
        filter_config = IntentFilterConfig.model_validate(
            yaml.safe_load((affect / "intent-filter-v1.yaml").read_text())
        )
        # A fitted source requires verified package admission, not silently reusing
        # this zero-residual factory when new coordinates appear in the workspace.
        if filter_config.retained_training_coordinates:
            raise ValueError("new support requires verified fitted package admission")
        schema = AffectVectorSchema.model_validate(
            yaml.safe_load((affect / "affect-vector-v1.yaml").read_text())
        )
        self._filter = IntentFilter(schema=schema, config=filter_config)
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(29)
            residual = ResidualStateSpace(residual_config)
        with torch.no_grad():
            for parameter in residual.parameters():
                parameter.zero_()
        hashes = {
            name: hashlib.sha256(config.model_dump_json().encode()).hexdigest()
            for name, config in (
                ("anchor", anchor),
                ("response", response),
                ("face", face),
                ("head", head),
                ("residual", residual_config),
                ("policy", policy),
                ("support", filter_config),
                ("affect", schema),
                ("sync", self.sync_config),
            )
        }
        hashes["weights"] = hashlib.sha256(save(residual.state_dict())).hexdigest()
        self.identity: dict[str, object] = {
            "composition_algorithm": "event-free-anchor/v2",
            "source": "authored-expression/v1"
            if mode == "authored"
            else "unfitted-neutral-fallback/v1",
            "trained_expression_model": False,
            "config_hashes": hashes,
            "support_basis": "authored-policy"
            if mode == "authored"
            else "no-demonstrated-support",
            "provenance": policy.provenance,
            "seed": seed,
            "generation_id": generation_id,
            "head_enabled": head_enabled,
            "origin_ns": origin_ns,
            "prefix_s": 0.4,
            "horizon_s": 1.0,
        }
        self._identity_hash = hashlib.sha256(
            json.dumps(self.identity, sort_keys=True).encode()
        ).hexdigest()
        composer = ProductionCandidateComposer(
            anchor_planner=anchor_planner,
            residual_model=residual,
            face_events=FaceEventGenerator(config=face, controller_config=response),
            head_scheduler=(
                HeadGestureScheduler if head_enabled else _DisabledHeadScheduler
            )(config=head, controller_config=response),
            controller_response=ControllerResponse(config=response),
        )
        self._runtime = StreamingMotionGenerator(
            generator=composer, horizon_s=1.0, prefix_duration_s=0.4
        )
        target = TargetUpdate(offset_s=0, targets=anchor.anchor("neutral").targets)
        self.state = GeneratorState(
            schema_version="generator-state/v1",
            last_accepted_target=target,
            last_reported_pose=target,
            estimated_velocity=tuple(
                ActuatorVelocity(actuator_name=t.actuator_name, velocity_per_s=0)
                for t in target.targets
            ),
            filtered_intent=FilteredIntent(
                schema_version="filtered-intent/v1",
                affect_schema_id="affect-vector/v1",
                vector=(0, 0, 0),
                intensity=0,
                source_id=generation_id,
                accepted_monotonic_ns=origin_ns,
                support_status=SupportStatus.FALLBACK,
                support_distance=None,
                reason="no demonstrated training support",
            ),
            latent_vector=(0.0,) * residual_config.hidden_size,
            numpy_rng_state=dict(np.random.default_rng(seed).bit_generator.state),
            torch_rng_state=tuple(
                int(v) for v in torch.Generator().manual_seed(seed).get_state()
            ),
            event_history=(),
            model_id=str(self.identity["source"]),
            model_sha256=self._identity_hash,
            calibration_sha256=anchor.calibration_sha256,
            controller_settings_sha256=anchor.controller_settings_sha256,
            monotonic_ns=origin_ns,
        )
        self._prefix: AcceptedPrefix | None = None
        self._boundary: GeneratorState | None = None
        self._last_frame: SpeechFrame | None = None
        self._last_sample = -1
        self._sample_rate: int | None = None
        self._cancelled = False

    def _intent(self, frame: SpeechFrame, at_ns: int, fresh: bool) -> FilteredIntent:
        previous = self.state.filtered_intent
        if not fresh:
            return previous.model_copy(
                update={
                    "support_status": SupportStatus.STALE,
                    "accepted_monotonic_ns": at_ns,
                    "reason": "stale audible source",
                }
            )
        if self.mode == "authored":
            return previous.model_copy(
                update={
                    "vector": frame.vector,
                    "intensity": frame.intensity,
                    "accepted_monotonic_ns": at_ns,
                    "support_status": SupportStatus.SUPPORTED,
                    "support_distance": None,
                    "reason": "authored policy enabled; not empirical training support",
                }
            )
        intent = AffectIntent(
            schema_version="affect-intent/v1",
            affect_schema_id="affect-vector/v1",
            vector=frame.vector,
            intensity=frame.intensity,
            source_id=previous.source_id,
            captured_at=datetime.now(timezone.utc),
            received_monotonic_ns=at_ns,
            expires_monotonic_ns=at_ns + 100000000,
        )
        return self._filter.update(intent, previous, at_ns)

    def advance(
        self,
        frame: SpeechFrame,
        played_sample: int,
        sample_rate: int,
        *,
        source_fresh: bool = True,
    ) -> TargetUpdate:
        if self._cancelled:
            raise RuntimeError("expression generation cancelled")
        if (
            frame.sample_index != played_sample
            or played_sample < self._last_sample
            or sample_rate <= 0
            or self._sample_rate not in (None, sample_rate)
        ):
            raise ValueError("stale frame, regressed sample or changed sample rate")
        self._sample_rate = sample_rate
        now = self.origin_ns + played_sample * 1000000000 // sample_rate
        while True:
            if self._prefix is not None and now >= self._prefix.ends_at_ns:
                assert self._boundary is not None
                self.state = self._boundary
                self._prefix = self._boundary = None
            if self._prefix is None:
                # A delayed callback must not apply the newest affect retroactively
                # to missed prefixes; use the previously audible observation there.
                request = (
                    self._last_frame
                    if self.state.monotonic_ns < now and self._last_frame
                    else frame
                )
                intent = self._intent(request, self.state.monotonic_ns, source_fresh)
                self._prefix, self._boundary = self._runtime.replan(
                    intent, self.state, self.state.monotonic_ns
                )
            if self._prefix.ends_at_ns > now:
                break
        positions = {
            t.actuator_name: t for t in self.state.last_accepted_target.targets
        }
        for update in self._prefix.updates:
            if self._prefix.starts_at_ns + round(update.offset_s * 1e9) > now:
                break
            positions.update({t.actuator_name: t for t in update.targets})
        self._last_sample, self._last_frame = played_sample, frame
        return TargetUpdate(
            offset_s=played_sample / sample_rate, targets=tuple(positions.values())
        )

    def cancel(self) -> None:
        self._cancelled = True
        self._prefix = self._boundary = None

    def snapshot(self) -> str:
        return _Snapshot(
            identity_sha256=self._identity_hash,
            state=self.state,
            prefix=self._prefix,
            boundary=self._boundary,
            last_frame=self._last_frame,
            last_sample=self._last_sample,
            sample_rate=self._sample_rate,
            cancelled=self._cancelled,
        ).model_dump_json()

    def restore(self, serialized: str) -> None:
        snapshot = _Snapshot.model_validate_json(serialized)
        if (
            snapshot.identity_sha256 != self._identity_hash
            or snapshot.state.model_sha256 != self._identity_hash
        ):
            raise ValueError("expression snapshot identity mismatch")
        if (snapshot.prefix is None) != (snapshot.boundary is None):
            raise ValueError("incomplete speculative prefix snapshot")
        self.state, self._prefix, self._boundary = (
            snapshot.state,
            snapshot.prefix,
            snapshot.boundary,
        )
        self._last_frame, self._last_sample = snapshot.last_frame, snapshot.last_sample
        self._sample_rate, self._cancelled = snapshot.sample_rate, snapshot.cancelled
