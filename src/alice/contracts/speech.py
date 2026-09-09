"""LLM-facing speech intentions, with no motor or device authority."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from alice.contracts.affect import AffectCoordinate, UnitIntervalFloat

Text = Annotated[str, Field(min_length=1, max_length=1000)]
Voice = Literal[
    "alba", "marius", "javert", "jean", "fantine", "cosette", "eponine", "azelma"
]
Vector = tuple[AffectCoordinate, AffectCoordinate, AffectCoordinate]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class SpeechCue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    progress: UnitIntervalFloat
    vector: Vector
    intensity: UnitIntervalFloat


class SpeechSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    text: Text
    cues: tuple[SpeechCue, ...] = Field(min_length=1, max_length=32)
    pause_after_s: float = Field(default=0, ge=0, le=5, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered_cues(self) -> SpeechSegment:
        if self.cues[0].progress != 0:
            raise ValueError("each segment must have a cue at progress zero")
        if any(b.progress <= a.progress for a, b in zip(self.cues, self.cues[1:])):
            raise ValueError("cue progress must be strictly increasing")
        return self


class SpeechPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["speech-plan/v1"]
    affect_schema_id: Literal["affect-vector/v1"] = "affect-vector/v1"
    utterance_id: Text
    source_id: Text
    seed: int = Field(default=0, ge=0, le=2**32 - 1)
    voice: Voice = "azelma"
    segments: tuple[SpeechSegment, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def bounded_text(self) -> SpeechPlan:
        if sum(len(segment.text) for segment in self.segments) > 4000:
            raise ValueError("utterance text exceeds 4000 characters")
        return self


class SpeechSyncConfig(BaseModel):
    """Software sync parameters; these are not measured servo limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["speech-sync/v1"] = "speech-sync/v1"
    cadence_hz: int = Field(default=50, ge=10, le=100)
    noise_gate_rms: float = Field(default=0.01, ge=0, lt=1, allow_inf_nan=False)
    full_open_rms: float = Field(default=0.15, gt=0, le=1, allow_inf_nan=False)
    attack_s: Positive = 0.03
    release_s: Positive = 0.06
    tail_s: float = Field(default=0.3, ge=0.1, le=2, allow_inf_nan=False)
    max_duration_s: float = Field(default=120, gt=0, le=600, allow_inf_nan=False)
    closed_position: AffectCoordinate = -1
    open_position: AffectCoordinate = 0.6
    expression_jaw_weight: UnitIntervalFloat = 0.15

    @model_validator(mode="after")
    def ordered_ranges(self) -> SpeechSyncConfig:
        if self.full_open_rms <= self.noise_gate_rms:
            raise ValueError("full opening RMS must exceed noise gate")
        if self.open_position <= self.closed_position:
            raise ValueError("opening position must exceed closed position")
        return self
