"""Conversions between generated ROS messages and validated Alice contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Literal

from alice_interfaces.action import RunSpeech
from alice_interfaces.msg import (
    ActuatorTarget as ActuatorTargetMsg,
)
from alice_interfaces.msg import (
    BlendshapeScore as BlendshapeScoreMsg,
)
from alice_interfaces.msg import (
    ExpressionFrame,
    FaceObservation,
    FaceTarget,
    PcmChunk,
    PcmCredit,
    PlaybackStatus,
    RunHealth,
    ServoReceipt,
    SpeechState,
)
from alice_interfaces.msg import PeerIdentity as PeerIdentityMsg
from alice_interfaces.msg import (
    RunIdentity as RunIdentityMsg,
)
from alice_interfaces.msg import (
    SpeechClause as SpeechClauseMsg,
)
from alice_interfaces.msg import (
    StreamHeader as StreamHeaderMsg,
)
from alice_interfaces.srv import BeginRun, EndRun
from builtin_interfaces.msg import Time

from alice.contracts import BlendshapeObservation, BlendshapeScore, ObservationValidity
from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.contracts.speech_stream import SpeechClause
from alice.speech.timeline import SpeechFrame
from alice_nodes.transport import (
    RUNTIME_NODE_NAMES,
    CreditLedger,
    PcmPacket,
    PeerIdentity,
    RunBinding,
    RunIdentity,
    SequenceGuard,
    StreamHeader,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PHASE_TO_WIRE = {
    "buffering": SpeechState.BUFFERING,
    "playing": SpeechState.PLAYING,
    "drained": SpeechState.DRAINED,
    "fault": SpeechState.FAULT,
}
_WIRE_TO_PHASE = {value: key for key, value in _PHASE_TO_WIRE.items()}


def _text(value: str, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"invalid {name}")
    return value


def _sha256(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


def _uint(value: int, name: str, bits: int = 64) -> int:
    if type(value) is not int or not 0 <= value < 2**bits:
        raise ValueError(f"invalid {name}")
    return value


def run_identity_to_msg(identity: RunIdentity) -> RunIdentityMsg:
    identity = RunIdentity(identity.run_id, identity.epoch, identity.generation_id)
    return RunIdentityMsg(
        run_id=identity.run_id,
        epoch=identity.epoch,
        generation_id=identity.generation_id,
    )


def run_identity_from_msg(message: RunIdentityMsg) -> RunIdentity:
    return RunIdentity(message.run_id, message.epoch, message.generation_id)


def stream_header_to_msg(header: StreamHeader) -> StreamHeaderMsg:
    return StreamHeaderMsg(
        identity=run_identity_to_msg(header.identity),
        sequence=header.sequence,
        source_monotonic_ns=header.source_monotonic_ns,
        publisher_incarnation=header.publisher_incarnation,
    )


def stream_header_from_msg(message: StreamHeaderMsg) -> StreamHeader:
    return StreamHeader(
        identity=run_identity_from_msg(message.identity),
        sequence=_uint(message.sequence, "stream sequence"),
        source_monotonic_ns=_uint(
            message.source_monotonic_ns, "source monotonic timestamp"
        ),
        publisher_incarnation=message.publisher_incarnation,
    )


def speech_clause_to_msg(clause: SpeechClause, header: StreamHeader) -> SpeechClauseMsg:
    clause = SpeechClause.model_validate(clause)
    if clause.generation_id != header.identity.generation_id:
        raise ValueError("clause generation does not match stream identity")
    return SpeechClauseMsg(
        header=stream_header_to_msg(header),
        schema_version=clause.schema_version,
        clause_id=clause.clause_id,
        clause_sequence=clause.sequence,
        text=clause.text,
        affect_vector=list(clause.vector),
        intensity=clause.intensity,
        seed=clause.seed,
        end_of_response=clause.end_of_response,
    )


def speech_clause_from_msg(message: SpeechClauseMsg) -> SpeechClause:
    header = stream_header_from_msg(message.header)
    clause = SpeechClause.model_validate(
        {
            "schema_version": message.schema_version,
            "generation_id": header.identity.generation_id,
            "clause_id": message.clause_id,
            "sequence": message.clause_sequence,
            "text": message.text,
            "vector": tuple(message.affect_vector),
            "intensity": message.intensity,
            "seed": message.seed,
            "end_of_response": message.end_of_response,
        }
    )
    return clause


def pcm_packet_to_msg(packet: PcmPacket) -> PcmChunk:
    clause = (
        speech_clause_to_msg(packet.clause, packet.header)
        if packet.clause is not None
        else SpeechClauseMsg()
    )
    return PcmChunk(
        header=stream_header_to_msg(packet.header),
        schema_version="pcm-chunk/v1",
        clause_id=packet.clause_id,
        clause_sequence=packet.clause_sequence,
        global_sample_offset=packet.global_sample_offset,
        sample_rate=packet.sample_rate,
        samples=list(packet.samples),
        first_packet=packet.first_packet,
        clause=clause,
        clause_final=packet.clause_final,
        response_final=packet.response_final,
    )


def pcm_packet_from_msg(message: PcmChunk) -> PcmPacket:
    if message.schema_version != "pcm-chunk/v1":
        raise ValueError("unknown PCM schema")
    header = stream_header_from_msg(message.header)
    clause = None
    if message.first_packet:
        clause_header = stream_header_from_msg(message.clause.header)
        if clause_header.identity != header.identity:
            raise ValueError("embedded clause identity does not match PCM identity")
        clause = speech_clause_from_msg(message.clause)
    return PcmPacket(
        header=header,
        clause_id=_text(message.clause_id, "clause ID", 128),
        clause_sequence=_uint(message.clause_sequence, "clause sequence", 32),
        global_sample_offset=_uint(
            message.global_sample_offset, "global sample offset"
        ),
        sample_rate=_uint(message.sample_rate, "sample rate", 32),
        samples=tuple(float(value) for value in message.samples),
        first_packet=bool(message.first_packet),
        clause=clause,
        clause_final=bool(message.clause_final),
        response_final=bool(message.response_final),
    )


def apply_pcm_credit(
    message: PcmCredit,
    ledger: CreditLedger,
    sequences: SequenceGuard,
    *,
    now_monotonic_ns: int,
) -> bool:
    """Validate a cumulative credit message before mutating either ledger."""

    header = stream_header_from_msg(message.header)
    if not sequences.validate(header, now_monotonic_ns=now_monotonic_ns):
        return False
    if message.schema_version != "pcm-credit/v1":
        raise ValueError("unknown PCM credit schema")
    if message.capacity_samples != ledger.capacity_samples:
        raise ValueError("PCM credit capacity changed within generation")
    changed = ledger.acknowledge(
        header.identity,
        cumulative_consumed_samples=message.cumulative_consumed_samples,
    )
    sequences.commit(header)
    return changed


def speech_state_to_msg(
    frame: SpeechFrame,
    header: StreamHeader,
    *,
    sample_rate: int,
    phase: Literal["buffering", "playing", "drained", "fault"],
    owner: str,
) -> SpeechState:
    frame = SpeechFrame.model_validate(frame)
    if not 8_000 <= _uint(sample_rate, "sample rate", 32) <= 192_000:
        raise ValueError("invalid sample rate")
    if phase not in _PHASE_TO_WIRE:
        raise ValueError("invalid playback phase")
    return SpeechState(
        header=stream_header_to_msg(header),
        schema_version="speech-state/v1",
        source_monotonic_ns=header.source_monotonic_ns,
        played_sample=frame.sample_index,
        sample_rate=sample_rate,
        envelope_aperture=frame.mouth_aperture,
        speech_weight=frame.speech_weight,
        audible_vector=list(frame.vector),
        audible_intensity=frame.intensity,
        phase=_PHASE_TO_WIRE[phase],
        owner_incarnation=_text(owner, "speech owner", 128),
    )


def speech_state_from_msg(message: SpeechState) -> SpeechFrame:
    header = stream_header_from_msg(message.header)
    if message.schema_version != "speech-state/v1":
        raise ValueError("unknown speech state schema")
    if message.source_monotonic_ns != header.source_monotonic_ns:
        raise ValueError("speech state lost its original source timestamp")
    if message.phase not in _WIRE_TO_PHASE:
        raise ValueError("invalid playback phase")
    if not 8_000 <= message.sample_rate <= 192_000:
        raise ValueError("invalid sample rate")
    _text(message.owner_incarnation, "speech owner", 128)
    return SpeechFrame.model_validate(
        {
            "sample_index": message.played_sample,
            "mouth_aperture": message.envelope_aperture,
            "speech_weight": message.speech_weight,
            "vector": tuple(message.audible_vector),
            "intensity": message.audible_intensity,
        }
    )


def _target_to_msg(target: ActuatorTarget) -> ActuatorTargetMsg:
    target = ActuatorTarget.model_validate(target)
    return ActuatorTargetMsg(
        actuator_name=target.actuator_name,
        normalized_position=target.normalized_position,
    )


def _target_from_msg(message: ActuatorTargetMsg) -> ActuatorTarget:
    return ActuatorTarget.model_validate(
        {
            "actuator_name": message.actuator_name,
            "normalized_position": message.normalized_position,
        }
    )


def expression_frame_to_msg(
    update: TargetUpdate,
    header: StreamHeader,
    speech_state: SpeechState,
    *,
    calibration_sha256: str,
) -> ExpressionFrame:
    update = TargetUpdate.model_validate(update)
    if len(update.targets) > 11:
        raise ValueError("expression exceeds target bound")
    if stream_header_from_msg(speech_state.header).identity != header.identity:
        raise ValueError("expression and speech state identities differ")
    speech_state_from_msg(speech_state)
    return ExpressionFrame(
        header=stream_header_to_msg(header),
        schema_version="expression-frame/v1",
        calibration_sha256=_sha256(calibration_sha256, "calibration hash"),
        speech_state=speech_state,
        targets=[_target_to_msg(target) for target in update.targets],
    )


def expression_frame_from_msg(
    message: ExpressionFrame, *, expected_calibration_sha256: str
) -> TargetUpdate:
    header = stream_header_from_msg(message.header)
    if message.schema_version != "expression-frame/v1":
        raise ValueError("unknown expression schema")
    if message.calibration_sha256 != _sha256(
        expected_calibration_sha256, "expected calibration hash"
    ):
        raise ValueError("expression calibration does not match")
    speech_header = stream_header_from_msg(message.speech_state.header)
    if (
        speech_header.identity != header.identity
        or speech_header.source_monotonic_ns != header.source_monotonic_ns
    ):
        raise ValueError("embedded speech state does not match expression source")
    speech_state_from_msg(message.speech_state)
    return TargetUpdate.model_validate(
        {
            "offset_s": 0.0,
            "targets": tuple(_target_from_msg(target) for target in message.targets),
        }
    )


def face_target_to_msg(
    update: TargetUpdate,
    header: StreamHeader,
    *,
    config_sha256: str,
    calibration_sha256: str,
    speech_sequence: int,
    played_sample: int,
) -> FaceTarget:
    update = TargetUpdate.model_validate(update)
    if len(update.targets) > 11:
        raise ValueError("face target exceeds target bound")
    return FaceTarget(
        header=stream_header_to_msg(header),
        schema_version="face-target/v1",
        config_sha256=_sha256(config_sha256, "config hash"),
        calibration_sha256=_sha256(calibration_sha256, "calibration hash"),
        speech_sequence=_uint(speech_sequence, "speech sequence"),
        played_sample=_uint(played_sample, "played sample"),
        speech_source_monotonic_ns=header.source_monotonic_ns,
        targets=[_target_to_msg(target) for target in update.targets],
    )


def face_target_from_msg(
    message: FaceTarget,
    *,
    expected_config_sha256: str,
    expected_calibration_sha256: str,
) -> TargetUpdate:
    header = stream_header_from_msg(message.header)
    if message.schema_version != "face-target/v1":
        raise ValueError("unknown face-target schema")
    if message.config_sha256 != _sha256(expected_config_sha256, "expected config hash"):
        raise ValueError("face-target config does not match")
    if message.calibration_sha256 != _sha256(
        expected_calibration_sha256, "expected calibration hash"
    ):
        raise ValueError("face-target calibration does not match")
    if message.speech_source_monotonic_ns != header.source_monotonic_ns:
        raise ValueError("face target lost its original speech source timestamp")
    _uint(message.speech_sequence, "speech sequence")
    _uint(message.played_sample, "played sample")
    return TargetUpdate.model_validate(
        {
            "offset_s": 0.0,
            "targets": tuple(_target_from_msg(target) for target in message.targets),
        }
    )


def _datetime_to_msg(value: datetime) -> Time:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    utc = value.astimezone(UTC)
    seconds = int(utc.timestamp())
    return Time(sec=seconds, nanosec=utc.microsecond * 1_000)


def _datetime_from_msg(value: Time) -> datetime:
    if not 0 <= value.nanosec < 1_000_000_000:
        raise ValueError("invalid timestamp nanoseconds")
    return datetime.fromtimestamp(value.sec, tz=UTC).replace(
        microsecond=value.nanosec // 1_000
    )


def face_observation_to_msg(
    observation: BlendshapeObservation, header: StreamHeader
) -> FaceObservation:
    observation = BlendshapeObservation.model_validate(observation)
    if observation.run_id != header.identity.run_id:
        raise ValueError("observation run does not match stream identity")
    if observation.monotonic_ns != header.source_monotonic_ns:
        raise ValueError("observation timestamp does not match stream source")
    if len(observation.scores) > 64:
        raise ValueError("observation exceeds score bound")
    observed = observation.observed_at or observation.captured_at
    return FaceObservation(
        header=stream_header_to_msg(header),
        schema_version=observation.schema_version,
        captured_at=_datetime_to_msg(observation.captured_at),
        has_observed_at=observation.observed_at is not None,
        observed_at=_datetime_to_msg(observed),
        camera_id=observation.camera_id,
        detector=observation.detector,
        detector_model_sha256=observation.detector_model_sha256,
        image_width=observation.image_width,
        image_height=observation.image_height,
        has_face_confidence=observation.face_confidence is not None,
        face_confidence=observation.face_confidence or 0.0,
        validity=(
            FaceObservation.VALID
            if observation.validity is ObservationValidity.VALID
            else FaceObservation.NO_FACE
        ),
        invalid_reason=observation.invalid_reason or "",
        scores=[
            BlendshapeScoreMsg(name=item.name, score=item.score)
            for item in observation.scores
        ],
    )


def face_observation_from_msg(message: FaceObservation) -> BlendshapeObservation:
    header = stream_header_from_msg(message.header)
    if message.schema_version != "blendshape-observation/v1":
        raise ValueError("unknown observation schema")
    validity = {
        FaceObservation.VALID: ObservationValidity.VALID,
        FaceObservation.NO_FACE: ObservationValidity.NO_FACE,
    }.get(message.validity)
    if validity is None:
        raise ValueError("invalid observation validity")
    if len(message.scores) > 64:
        raise ValueError("observation exceeds score bound")
    return BlendshapeObservation.model_validate(
        {
            "schema_version": message.schema_version,
            "captured_at": _datetime_from_msg(message.captured_at),
            "observed_at": (
                _datetime_from_msg(message.observed_at)
                if message.has_observed_at
                else None
            ),
            "monotonic_ns": header.source_monotonic_ns,
            "camera_id": message.camera_id,
            "run_id": header.identity.run_id,
            "detector": message.detector,
            "detector_model_sha256": message.detector_model_sha256,
            "image_width": message.image_width,
            "image_height": message.image_height,
            "face_confidence": (
                message.face_confidence if message.has_face_confidence else None
            ),
            "validity": validity,
            "invalid_reason": message.invalid_reason or None,
            "scores": tuple(
                BlendshapeScore(name=item.name, score=item.score)
                for item in message.scores
            ),
        }
    )


@dataclass(frozen=True)
class BeginRunCommand:
    identity: RunIdentity
    operation: Literal["prepare", "start"]
    selected_profile: str
    seed: int
    hardware: bool
    sad_hold_ms: int
    config_sha256: str
    calibration_sha256: str
    clock_domain_fingerprint: str
    requester_incarnation: str
    peers: tuple[PeerIdentity, ...]

    @property
    def binding(self) -> RunBinding:
        return RunBinding(
            identity=self.identity,
            selected_profile=self.selected_profile,
            seed=self.seed,
            hardware=self.hardware,
            sad_hold_ms=self.sad_hold_ms,
            config_sha256=self.config_sha256,
            calibration_sha256=self.calibration_sha256,
            clock_domain_fingerprint=self.clock_domain_fingerprint,
            requester_incarnation=self.requester_incarnation,
            peers=self.peers,
        )


@dataclass(frozen=True)
class EndRunCommand:
    identity: RunIdentity
    outcome: Literal["success", "cancelled", "fault"]
    reason: str
    requester_incarnation: str


@dataclass(frozen=True)
class RunSpeechCommand:
    identity: RunIdentity
    source: Literal["fixture", "committed_clauses"]
    fixture_name: str
    selected_profile: str
    seed: int
    hardware: bool
    sad_hold_ms: int
    config_sha256: str
    calibration_sha256: str
    clock_domain_fingerprint: str
    requester_incarnation: str


@dataclass(frozen=True)
class EndRunAcknowledgement:
    identity: RunIdentity
    accepted: bool
    idempotent: bool
    completed: bool
    terminal_outcome: Literal["success", "cancelled", "fault"]
    lifecycle_state: Literal["finalizing", "completed"]
    artifact_identity: str | None
    responder_incarnation: str
    error: str | None


@dataclass(frozen=True)
class RunHealthValue:
    header: StreamHeader
    state: Literal[
        "preparing",
        "ready",
        "active",
        "fault",
        "cancelled",
        "finalizing",
        "completed",
    ]
    progress: int
    detail: str | None


@dataclass(frozen=True)
class PlaybackStatusValue:
    header: StreamHeader
    state: Literal["buffering", "playing", "drained", "fault", "cancelled"]
    submitted_samples: int
    played_samples: int
    response_final_seen: bool
    drained: bool
    error: str | None


@dataclass(frozen=True)
class ServoChannelValue:
    actuator_name: str
    channel: int
    target_qus: int
    observed_qus: int


@dataclass(frozen=True)
class ServoReceiptValue:
    header: StreamHeader
    request_id: str
    hardware_id: str
    calibration_sha256: str
    state: Literal["applied", "rejected", "fault"]
    channels: tuple[ServoChannelValue, ...]
    fault_code: str | None
    detail: str | None


def _peer_from_msg(message: PeerIdentityMsg) -> PeerIdentity:
    return PeerIdentity(
        node_name=_text(message.node_name, "peer node name", 32),
        incarnation=_text(message.incarnation, "peer incarnation", 128),
    )


def _validate_run_fields(message: object) -> dict[str, object]:
    sad_hold_ms = _uint(getattr(message, "sad_hold_ms"), "sad hold", 32)
    if sad_hold_ms > 2_000:
        raise ValueError("sad hold exceeds two-second limit")
    seed = _uint(getattr(message, "seed"), "seed", 32)
    hardware = getattr(message, "hardware")
    if type(hardware) is not bool:
        raise ValueError("hardware flag must be boolean")
    return {
        "identity": run_identity_from_msg(getattr(message, "identity")),
        "selected_profile": _text(
            getattr(message, "selected_profile"), "selected profile", 64
        ),
        "seed": seed,
        "hardware": hardware,
        "sad_hold_ms": sad_hold_ms,
        "config_sha256": _sha256(getattr(message, "config_sha256"), "config hash"),
        "calibration_sha256": _sha256(
            getattr(message, "calibration_sha256"), "calibration hash"
        ),
        "clock_domain_fingerprint": _text(
            getattr(message, "clock_domain_fingerprint"),
            "clock-domain fingerprint",
            128,
        ),
        "requester_incarnation": _text(
            getattr(message, "requester_incarnation"), "requester incarnation", 128
        ),
    }


def validate_begin_run_request(request: BeginRun.Request) -> BeginRunCommand:
    if request.schema_version != "begin-run/v1":
        raise ValueError("unknown begin-run schema")
    operation = {
        BeginRun.Request.PREPARE: "prepare",
        BeginRun.Request.START: "start",
    }.get(request.operation)
    if operation is None:
        raise ValueError("invalid begin-run operation")
    peers = tuple(_peer_from_msg(peer) for peer in request.peers)
    names = [peer.node_name for peer in peers]
    if len(names) != len(set(names)):
        raise ValueError("peer roster has duplicate node names")
    if operation == "prepare" and peers:
        raise ValueError("prepare peer roster must be empty")
    if operation == "start" and set(names) != RUNTIME_NODE_NAMES:
        raise ValueError("start peer roster must identify all eight runtime nodes")
    return BeginRunCommand(
        operation=operation,
        peers=peers,
        **_validate_run_fields(request),
    )


def validate_end_run_request(request: EndRun.Request) -> EndRunCommand:
    if request.schema_version != "end-run/v1":
        raise ValueError("unknown end-run schema")
    outcome = {
        EndRun.Request.SUCCESS: "success",
        EndRun.Request.CANCELLED: "cancelled",
        EndRun.Request.FAULT: "fault",
    }.get(request.outcome)
    if outcome is None:
        raise ValueError("invalid terminal outcome")
    return EndRunCommand(
        identity=run_identity_from_msg(request.identity),
        outcome=outcome,
        reason=_text(request.reason, "terminal reason", 256),
        requester_incarnation=_text(
            request.requester_incarnation, "requester incarnation", 128
        ),
    )


def validate_end_run_response(
    response: EndRun.Response, *, expected_identity: RunIdentity
) -> EndRunAcknowledgement:
    identity = run_identity_from_msg(response.identity)
    if identity != expected_identity:
        raise ValueError("end-run response identity does not match")
    outcome = {
        EndRun.Response.RESULT_SUCCESS: "success",
        EndRun.Response.RESULT_CANCELLED: "cancelled",
        EndRun.Response.RESULT_FAULT: "fault",
    }.get(response.terminal_outcome)
    lifecycle = {
        EndRun.Response.STATE_FINALIZING: "finalizing",
        EndRun.Response.STATE_COMPLETED: "completed",
    }.get(response.lifecycle_state)
    if outcome is None:
        raise ValueError("invalid terminal outcome")
    if lifecycle is None:
        raise ValueError("invalid end-run lifecycle state")
    if response.completed != (lifecycle == "completed"):
        raise ValueError("completed flag disagrees with end-run lifecycle")
    error = response.error or None
    artifact = response.artifact_identity or None
    if response.accepted and error is not None:
        raise ValueError("accepted end-run response cannot carry an error")
    if not response.accepted and error is None:
        raise ValueError("rejected end-run response requires an error")
    if response.completed and not response.accepted:
        raise ValueError("rejected end-run response cannot be completed")
    if response.completed and outcome == "success" and artifact is None:
        raise ValueError("successful completion requires artifact identity")
    return EndRunAcknowledgement(
        identity=identity,
        accepted=bool(response.accepted),
        idempotent=bool(response.idempotent),
        completed=bool(response.completed),
        terminal_outcome=outcome,
        lifecycle_state=lifecycle,
        artifact_identity=artifact,
        responder_incarnation=_text(
            response.responder_incarnation, "responder incarnation", 128
        ),
        error=error,
    )


def validate_run_speech_goal(goal: RunSpeech.Goal) -> RunSpeechCommand:
    if goal.schema_version != "run-speech/v1":
        raise ValueError("unknown run-speech schema")
    source = {
        RunSpeech.Goal.FIXTURE: "fixture",
        RunSpeech.Goal.COMMITTED_CLAUSES: "committed_clauses",
    }.get(goal.source)
    if source is None:
        raise ValueError("invalid speech source")
    fixture_name = goal.fixture_name
    if source == "fixture":
        fixture_name = _text(fixture_name, "fixture name", 128)
        path = PurePosixPath(fixture_name)
        if (
            path.name != fixture_name
            or fixture_name in {".", ".."}
            or "\\" in fixture_name
        ):
            raise ValueError("fixture must be one mounted file name")
    elif fixture_name:
        raise ValueError("committed-clause source must not name a fixture")
    return RunSpeechCommand(
        source=source,
        fixture_name=fixture_name,
        **_validate_run_fields(goal),
    )


def validate_run_health(message: RunHealth) -> RunHealthValue:
    header = stream_header_from_msg(message.header)
    if message.schema_version != "run-health/v1":
        raise ValueError("unknown run-health schema")
    state = {
        RunHealth.PREPARING: "preparing",
        RunHealth.READY: "ready",
        RunHealth.ACTIVE: "active",
        RunHealth.FAULT: "fault",
        RunHealth.CANCELLED: "cancelled",
        RunHealth.FINALIZING: "finalizing",
        RunHealth.COMPLETED: "completed",
    }.get(message.state)
    if state is None:
        raise ValueError("invalid run-health state")
    detail = message.detail or None
    if detail is not None:
        _text(detail, "run-health detail", 256)
    if state in {"fault", "cancelled"} and detail is None:
        raise ValueError("fault or cancellation health requires detail")
    return RunHealthValue(
        header=header,
        state=state,
        progress=_uint(message.progress, "run-health progress"),
        detail=detail,
    )


def validate_playback_status(message: PlaybackStatus) -> PlaybackStatusValue:
    header = stream_header_from_msg(message.header)
    if message.schema_version != "playback-status/v1":
        raise ValueError("unknown playback-status schema")
    state = {
        PlaybackStatus.BUFFERING: "buffering",
        PlaybackStatus.PLAYING: "playing",
        PlaybackStatus.DRAINED: "drained",
        PlaybackStatus.FAULT: "fault",
        PlaybackStatus.CANCELLED: "cancelled",
    }.get(message.state)
    if state is None:
        raise ValueError("invalid playback state")
    submitted = _uint(message.submitted_samples, "submitted sample count")
    played = _uint(message.played_samples, "played sample count")
    if played > submitted:
        raise ValueError("played samples cannot exceed submitted samples")
    error = message.error or None
    if state == "drained":
        if (
            not message.drained
            or not message.response_final_seen
            or played != submitted
            or error is not None
        ):
            raise ValueError("playback drain lacks final equal-count evidence")
    elif message.drained:
        raise ValueError("only drained playback state may assert drain")
    if state == "fault" and error is None:
        raise ValueError("fault playback status requires an error")
    return PlaybackStatusValue(
        header=header,
        state=state,
        submitted_samples=submitted,
        played_samples=played,
        response_final_seen=bool(message.response_final_seen),
        drained=bool(message.drained),
        error=error,
    )


_SELECTED_SERVO_CHANNELS = {
    3: "lower_eyelids",
    4: "upper_eyelids",
    5: "forehead_frown",
    6: "mouth_open",
    9: "left_mouth_corner",
    11: "right_mouth_corner",
}


def validate_servo_receipt(
    message: ServoReceipt, *, expected_calibration_sha256: str
) -> ServoReceiptValue:
    header = stream_header_from_msg(message.header)
    if message.schema_version != "servo-receipt/v1":
        raise ValueError("unknown servo-receipt schema")
    calibration = _sha256(message.calibration_sha256, "calibration hash")
    if calibration != _sha256(expected_calibration_sha256, "expected calibration hash"):
        raise ValueError("servo receipt calibration does not match")
    state = {
        ServoReceipt.APPLIED: "applied",
        ServoReceipt.REJECTED: "rejected",
        ServoReceipt.FAULT: "fault",
    }.get(message.state)
    if state is None:
        raise ValueError("invalid servo receipt state")
    channels: list[ServoChannelValue] = []
    names: set[str] = set()
    numbers: set[int] = set()
    for channel in message.channels:
        name = _text(channel.actuator_name, "servo actuator name", 64)
        if _SELECTED_SERVO_CHANNELS.get(channel.channel) != name:
            raise ValueError("servo receipt channel/name is outside selected mapping")
        if name in names or channel.channel in numbers:
            raise ValueError("servo receipt channels and names must be unique")
        if not 1 <= channel.target_qus <= 16_383:
            raise ValueError("target PWM is outside Maestro wire range")
        if not 0 <= channel.observed_qus <= 16_383:
            raise ValueError("observed PWM is outside Maestro wire range")
        names.add(name)
        numbers.add(channel.channel)
        channels.append(
            ServoChannelValue(
                actuator_name=name,
                channel=channel.channel,
                target_qus=channel.target_qus,
                observed_qus=channel.observed_qus,
            )
        )
    fault_code = message.fault_code or None
    detail = message.detail or None
    if state == "applied":
        if not channels or fault_code is not None:
            raise ValueError("applied receipt requires channels and no fault")
    elif fault_code is None:
        raise ValueError("rejected or fault receipt requires a fault code")
    if detail is not None:
        _text(detail, "servo receipt detail", 256)
    return ServoReceiptValue(
        header=header,
        request_id=_text(message.request_id, "servo request ID", 128),
        hardware_id=_text(message.hardware_id, "hardware ID", 64),
        calibration_sha256=calibration,
        state=state,
        channels=tuple(channels),
        fault_code=fault_code,
        detail=detail,
    )
