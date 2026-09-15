from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest
from alice_interfaces.action import RunSpeech
from alice_interfaces.msg import (
    FaceTarget,
    PcmChunk,
    PcmCredit,
    PlaybackStatus,
    RunHealth,
    ServoChannelReceipt,
    ServoReceipt,
)
from alice_interfaces.msg import (
    PeerIdentity as PeerIdentityMsg,
)
from alice_interfaces.msg import RunIdentity as RunIdentityMsg
from alice_interfaces.srv import BeginRun, EndRun
from alice_nodes.contracts import (
    apply_pcm_credit,
    expression_frame_from_msg,
    expression_frame_to_msg,
    face_observation_from_msg,
    face_observation_to_msg,
    face_target_from_msg,
    face_target_to_msg,
    pcm_packet_from_msg,
    pcm_packet_to_msg,
    run_identity_from_msg,
    run_identity_to_msg,
    speech_clause_from_msg,
    speech_clause_to_msg,
    speech_state_from_msg,
    speech_state_to_msg,
    validate_begin_run_request,
    validate_begin_run_response,
    validate_end_run_request,
    validate_end_run_response,
    validate_playback_status,
    validate_run_health,
    validate_run_speech_feedback,
    validate_run_speech_goal,
    validate_run_speech_result,
    validate_servo_receipt,
)
from alice_nodes.transport import (
    CreditLedger,
    PcmPacket,
    PeerIdentity,
    RunIdentity,
    SequenceGuard,
    StreamHeader,
)

from alice.contracts import BlendshapeObservation, BlendshapeScore, ObservationValidity
from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.contracts.speech_stream import SpeechClause
from alice.speech.timeline import SpeechFrame

HASH = "a" * 64
IDENTITY = RunIdentity("run-1", "epoch-1", "gen-1")
HEADER = StreamHeader(IDENTITY, 3, 1_000_000_000, "expression-1")
PEER_NAMES = (
    "session",
    "tts",
    "audio",
    "expression",
    "motion",
    "maestro",
    "perception",
    "recorder",
)


def _clause() -> SpeechClause:
    return SpeechClause(
        schema_version="speech-clause/v1",
        generation_id="gen-1",
        clause_id="clause-0",
        sequence=0,
        text="Hello",
        vector=(0.1, -0.2, 0.3),
        intensity=0.4,
        seed=7,
        end_of_response=True,
    )


def test_run_identity_round_trips_through_generated_message() -> None:
    wire = run_identity_to_msg(IDENTITY)

    assert isinstance(wire, RunIdentityMsg)
    assert run_identity_from_msg(wire) == IDENTITY


def test_speech_clause_round_trips_through_generated_message() -> None:
    wire = speech_clause_to_msg(_clause(), HEADER)

    assert speech_clause_from_msg(wire) == _clause()
    assert wire.header.publisher_incarnation == "expression-1"


def test_speech_clause_rejects_unknown_schema() -> None:
    wire = speech_clause_to_msg(_clause(), HEADER)
    wire.schema_version = "speech-clause/v9"
    with pytest.raises(ValueError):
        speech_clause_from_msg(wire)


def test_speech_clause_rejects_wrong_vector_width() -> None:
    wire = speech_clause_to_msg(_clause(), HEADER)
    wire.affect_vector = [0.1, 0.2]

    with pytest.raises(ValueError):
        speech_clause_from_msg(wire)


def test_pcm_packet_round_trips_real_generated_message_with_first_clause() -> None:
    packet = PcmPacket(
        header=HEADER,
        clause_id="clause-0",
        clause_sequence=0,
        global_sample_offset=0,
        sample_rate=16_000,
        samples=(0.0, 0.25),
        first_packet=True,
        clause=_clause(),
        clause_final=True,
        response_final=True,
    )
    wire = pcm_packet_to_msg(packet)

    assert isinstance(wire, PcmChunk)
    assert pcm_packet_from_msg(wire) == packet


def test_pcm_packet_rejects_mismatched_nested_clause_identity() -> None:
    packet = PcmPacket(
        header=HEADER,
        clause_id="clause-0",
        clause_sequence=0,
        global_sample_offset=0,
        sample_rate=16_000,
        samples=(0.0,),
        first_packet=True,
        clause=_clause(),
        clause_final=True,
        response_final=True,
    )
    wire = pcm_packet_to_msg(packet)
    wire.clause.header.identity.run_id = "other-run"

    with pytest.raises(ValueError, match="identity"):
        pcm_packet_from_msg(wire)


def test_generated_cumulative_credit_updates_ledger_transactionally() -> None:
    ledger = CreditLedger(IDENTITY, capacity_samples=200)
    ledger.reserve(150)
    sequences = SequenceGuard(IDENTITY, exact=True)
    credit = PcmCredit()
    credit.header = pcm_packet_to_msg(
        PcmPacket(
            header=StreamHeader(IDENTITY, 0, 1_000_000_000, "audio-1"),
            clause_id="clause-0",
            clause_sequence=0,
            global_sample_offset=0,
            sample_rate=16_000,
            samples=(0.0,),
            first_packet=True,
            clause=_clause(),
            clause_final=True,
            response_final=True,
        )
    ).header
    credit.schema_version = "pcm-credit/v1"
    credit.capacity_samples = 200
    credit.cumulative_consumed_samples = 100

    assert apply_pcm_credit(credit, ledger, sequences, now_monotonic_ns=1_000_000_000)
    assert ledger.available_samples == 150

    bad = PcmCredit()
    bad.header = credit.header
    bad.header.sequence = 1
    bad.capacity_samples = 201
    bad.schema_version = "pcm-credit/v1"
    bad.cumulative_consumed_samples = 120
    with pytest.raises(ValueError, match="capacity"):
        apply_pcm_credit(bad, ledger, sequences, now_monotonic_ns=1_000_000_000)

    bad.capacity_samples = 200
    assert apply_pcm_credit(bad, ledger, sequences, now_monotonic_ns=1_000_000_000)
    assert ledger.available_samples == 170


def test_speech_state_round_trips_existing_domain_frame() -> None:
    frame = SpeechFrame(
        sample_index=400,
        mouth_aperture=0.2,
        speech_weight=0.7,
        vector=(0.1, -0.2, 0.3),
        intensity=0.5,
    )
    wire = speech_state_to_msg(
        frame,
        HEADER,
        sample_rate=16_000,
        phase="playing",
        owner="audio-1",
    )

    assert speech_state_from_msg(wire) == frame
    assert wire.played_sample == 400
    assert wire.source_monotonic_ns == HEADER.source_monotonic_ns


def test_speech_state_rejects_nonfinite_value() -> None:
    frame = SpeechFrame(
        sample_index=400,
        mouth_aperture=0.2,
        speech_weight=0.7,
        vector=(0.1, -0.2, 0.3),
        intensity=0.5,
    )
    wire = speech_state_to_msg(
        frame,
        HEADER,
        sample_rate=16_000,
        phase="playing",
        owner="audio-1",
    )
    wire.envelope_aperture = float("nan")

    with pytest.raises(ValueError):
        speech_state_from_msg(wire)


def test_expression_frame_round_trips_target_update_and_checks_calibration() -> None:
    update = TargetUpdate(
        offset_s=0.0,
        targets=(ActuatorTarget(actuator_name="jaw", normalized_position=0.25),),
    )
    state = SpeechFrame(
        sample_index=400,
        mouth_aperture=0.2,
        speech_weight=0.7,
        vector=(0.1, -0.2, 0.3),
        intensity=0.5,
    )
    speech = speech_state_to_msg(
        state, HEADER, sample_rate=16_000, phase="playing", owner="audio-1"
    )
    wire = expression_frame_to_msg(update, HEADER, speech, calibration_sha256=HASH)

    assert expression_frame_from_msg(wire, expected_calibration_sha256=HASH) == update
    with pytest.raises(ValueError, match="calibration"):
        expression_frame_from_msg(wire, expected_calibration_sha256="b" * 64)


def test_expression_frame_rejects_mismatched_embedded_speech_identity() -> None:
    update = TargetUpdate(
        offset_s=0.0,
        targets=(ActuatorTarget(actuator_name="jaw", normalized_position=0.25),),
    )
    other_header = StreamHeader(
        RunIdentity("other", "epoch-1", "gen-1"), 3, 1_000_000_000, "audio-1"
    )
    frame = SpeechFrame(
        sample_index=400,
        mouth_aperture=0.2,
        speech_weight=0.7,
        vector=(0.1, -0.2, 0.3),
        intensity=0.5,
    )
    speech = speech_state_to_msg(
        frame, other_header, sample_rate=16_000, phase="playing", owner="audio-1"
    )
    wire = expression_frame_to_msg(
        update, other_header, speech, calibration_sha256=HASH
    )
    wire.header.identity = run_identity_to_msg(IDENTITY)

    with pytest.raises((AssertionError, TypeError, ValueError)):
        expression_frame_from_msg(wire, expected_calibration_sha256=HASH)


def test_face_target_round_trips_original_source_and_target_update() -> None:
    update = TargetUpdate(
        offset_s=0.0,
        targets=(ActuatorTarget(actuator_name="jaw", normalized_position=0.25),),
    )
    wire = face_target_to_msg(
        update,
        HEADER,
        config_sha256="b" * 64,
        calibration_sha256=HASH,
        speech_sequence=2,
        played_sample=400,
    )

    assert isinstance(wire, FaceTarget)
    assert (
        face_target_from_msg(
            wire,
            expected_config_sha256="b" * 64,
            expected_calibration_sha256=HASH,
        )
        == update
    )
    assert wire.speech_source_monotonic_ns == HEADER.source_monotonic_ns


def test_face_observation_round_trips_existing_domain_contract() -> None:
    observation = BlendshapeObservation(
        schema_version="blendshape-observation/v1",
        captured_at=datetime(2026, 9, 15, tzinfo=UTC),
        observed_at=datetime(2026, 9, 15, 0, 0, 1, tzinfo=UTC),
        monotonic_ns=1_000_000_000,
        camera_id="c525",
        run_id="run-1",
        detector="mediapipe",
        detector_model_sha256=HASH,
        image_width=640,
        image_height=480,
        face_confidence=0.9,
        validity=ObservationValidity.VALID,
        invalid_reason=None,
        scores=(BlendshapeScore(name="jawOpen", score=0.25),),
    )
    wire = face_observation_to_msg(observation, HEADER)

    assert face_observation_from_msg(wire) == observation


def test_begin_run_prepare_and_start_are_typed_and_bounded() -> None:
    prepare = BeginRun.Request()
    prepare.schema_version = "begin-run/v1"
    prepare.identity = run_identity_to_msg(IDENTITY)
    prepare.operation = BeginRun.Request.PREPARE
    prepare.selected_profile = "bench"
    prepare.seed = 7
    prepare.hardware = True
    prepare.sad_hold_ms = 2_000
    prepare.config_sha256 = HASH
    prepare.calibration_sha256 = HASH
    prepare.clock_domain_fingerprint = "salted-fingerprint"
    prepare.requester_incarnation = "session-1"

    command = validate_begin_run_request(prepare)
    assert command.operation == "prepare"
    assert command.identity == IDENTITY

    prepare.sad_hold_ms = 2_001
    with pytest.raises(ValueError, match="sad hold"):
        validate_begin_run_request(prepare)


def test_begin_run_start_requires_exact_unique_peer_roster() -> None:
    request = BeginRun.Request()
    request.schema_version = "begin-run/v1"
    request.identity = run_identity_to_msg(IDENTITY)
    request.operation = BeginRun.Request.START
    request.selected_profile = "bench"
    request.seed = 7
    request.hardware = True
    request.sad_hold_ms = 0
    request.config_sha256 = HASH
    request.calibration_sha256 = HASH
    request.clock_domain_fingerprint = "salted-fingerprint"
    request.requester_incarnation = "session-1"
    request.peers = [
        PeerIdentityMsg(node_name=name, incarnation=f"{name}-1") for name in PEER_NAMES
    ]

    assert len(validate_begin_run_request(request).peers) == 8
    request.peers[-1] = PeerIdentityMsg(node_name="session", incarnation="replacement")
    with pytest.raises(ValueError, match="peer"):
        validate_begin_run_request(request)


def test_begin_run_rejects_unknown_schema() -> None:
    request = BeginRun.Request()
    request.schema_version = "begin-run/v9"
    request.identity = run_identity_to_msg(IDENTITY)
    request.operation = BeginRun.Request.PREPARE
    request.selected_profile = "bench"
    request.seed = 7
    request.hardware = False
    request.sad_hold_ms = 0
    request.config_sha256 = HASH
    request.calibration_sha256 = HASH
    request.clock_domain_fingerprint = "salted-fingerprint"
    request.requester_incarnation = "session-1"

    with pytest.raises(ValueError, match="schema"):
        validate_begin_run_request(request)


def test_begin_run_response_validates_expected_peer_context_and_is_frozen() -> None:
    response = BeginRun.Response()
    response.identity = run_identity_to_msg(IDENTITY)
    response.accepted = True
    response.idempotent = False
    response.error = ""
    response.responder_node_name = "tts"
    response.responder_incarnation = "tts-1"
    response.clock_verified = False
    response.lifecycle_state = BeginRun.Response.PREPARED

    acknowledgement = validate_begin_run_response(
        response,
        expected_identity=IDENTITY,
        expected_responder=PeerIdentity("tts", "tts-1"),
        requested_operation="prepare",
        hardware=False,
    )
    assert acknowledgement.lifecycle_state == "prepared"
    with pytest.raises(FrozenInstanceError):
        acknowledgement.accepted = False  # type: ignore[misc]

    response.identity = run_identity_to_msg(
        RunIdentity("other-run", "epoch-1", "gen-1")
    )
    with pytest.raises(ValueError, match="identity"):
        validate_begin_run_response(
            response,
            expected_identity=IDENTITY,
            expected_responder=PeerIdentity("tts", "tts-1"),
            requested_operation="prepare",
            hardware=False,
        )
    response.identity = run_identity_to_msg(IDENTITY)
    response.responder_incarnation = "tts-restarted"
    with pytest.raises(ValueError, match="responder"):
        validate_begin_run_response(
            response,
            expected_identity=IDENTITY,
            expected_responder=PeerIdentity("tts", "tts-1"),
            requested_operation="prepare",
            hardware=False,
        )


def test_begin_run_response_rejects_inconsistent_acceptance_clock_and_state() -> None:
    response = BeginRun.Response()
    response.identity = run_identity_to_msg(IDENTITY)
    response.accepted = True
    response.error = "unexpected"
    response.responder_node_name = "maestro"
    response.responder_incarnation = "maestro-1"
    response.clock_verified = False
    response.lifecycle_state = BeginRun.Response.PREPARED
    context = {
        "expected_identity": IDENTITY,
        "expected_responder": PeerIdentity("maestro", "maestro-1"),
        "requested_operation": "start",
        "hardware": True,
    }

    with pytest.raises(ValueError, match="accepted"):
        validate_begin_run_response(response, **context)
    response.error = ""
    response.lifecycle_state = BeginRun.Response.ACTIVE
    with pytest.raises(ValueError, match="clock"):
        validate_begin_run_response(response, **context)
    response.clock_verified = True
    response.lifecycle_state = BeginRun.Response.PREPARED
    with pytest.raises(ValueError, match="lifecycle"):
        validate_begin_run_response(response, **context)


def test_end_run_requires_explicit_terminal_outcome() -> None:
    request = EndRun.Request()
    request.schema_version = "end-run/v1"
    request.identity = run_identity_to_msg(IDENTITY)
    request.outcome = EndRun.Request.SUCCESS
    request.reason = "drained"
    request.requester_incarnation = "session-1"

    assert validate_end_run_request(request).outcome == "success"
    request.outcome = 99
    with pytest.raises(ValueError, match="outcome"):
        validate_end_run_request(request)


def test_end_run_acceptance_is_distinct_from_completed_evidence() -> None:
    response = EndRun.Response()
    response.identity = run_identity_to_msg(IDENTITY)
    response.accepted = True
    response.idempotent = False
    response.completed = False
    response.error = ""
    response.terminal_outcome = EndRun.Response.RESULT_SUCCESS
    response.lifecycle_state = EndRun.Response.STATE_FINALIZING
    response.artifact_identity = ""
    response.responder_incarnation = "recorder-1"

    assert (
        validate_end_run_response(response, expected_identity=IDENTITY).completed
        is False
    )
    response.completed = True
    response.lifecycle_state = EndRun.Response.STATE_COMPLETED
    with pytest.raises(ValueError, match="artifact"):
        validate_end_run_response(response, expected_identity=IDENTITY)

    response.artifact_identity = "manifest-sha256"
    assert validate_end_run_response(response, expected_identity=IDENTITY).completed


def test_run_speech_fixture_name_cannot_escape_mount() -> None:
    goal = RunSpeech.Goal()
    goal.schema_version = "run-speech/v1"
    goal.identity = run_identity_to_msg(IDENTITY)
    goal.source = RunSpeech.Goal.FIXTURE
    goal.fixture_name = "stream-demo-v1.jsonl"
    goal.selected_profile = "bench"
    goal.seed = 7
    goal.hardware = False
    goal.sad_hold_ms = 0
    goal.config_sha256 = HASH
    goal.calibration_sha256 = HASH
    goal.clock_domain_fingerprint = "salted-fingerprint"
    goal.requester_incarnation = "session-1"
    assert validate_run_speech_goal(goal).fixture_name == "stream-demo-v1.jsonl"

    goal.fixture_name = "../secret"
    with pytest.raises(ValueError, match="fixture"):
        validate_run_speech_goal(goal)


def test_committed_clause_action_source_has_no_fixture_path() -> None:
    goal = RunSpeech.Goal()
    goal.schema_version = "run-speech/v1"
    goal.identity = run_identity_to_msg(IDENTITY)
    goal.source = RunSpeech.Goal.COMMITTED_CLAUSES
    goal.fixture_name = "unexpected.jsonl"
    goal.selected_profile = "bench"
    goal.seed = 7
    goal.hardware = False
    goal.sad_hold_ms = 0
    goal.config_sha256 = HASH
    goal.calibration_sha256 = HASH
    goal.clock_domain_fingerprint = "salted-fingerprint"
    goal.requester_incarnation = "session-1"

    with pytest.raises(ValueError, match="fixture"):
        validate_run_speech_goal(goal)


def test_run_speech_result_validates_identity_outcome_and_artifact() -> None:
    result = RunSpeech.Result()
    result.identity = run_identity_to_msg(IDENTITY)
    result.accepted = True
    result.error = ""
    result.terminal_outcome = RunSpeech.Result.SUCCESS
    result.artifact_identity = "manifest-sha256"
    result.responder_incarnation = "session-1"

    value = validate_run_speech_result(
        result,
        expected_identity=IDENTITY,
        expected_responder_incarnation="session-1",
    )
    assert value.terminal_outcome == "success"
    with pytest.raises(FrozenInstanceError):
        value.accepted = False  # type: ignore[misc]

    result.artifact_identity = ""
    with pytest.raises(ValueError, match="artifact"):
        validate_run_speech_result(
            result,
            expected_identity=IDENTITY,
            expected_responder_incarnation="session-1",
        )
    result.artifact_identity = "manifest-sha256"
    result.terminal_outcome = 99
    with pytest.raises(ValueError, match="outcome"):
        validate_run_speech_result(
            result,
            expected_identity=IDENTITY,
            expected_responder_incarnation="session-1",
        )
    result.terminal_outcome = RunSpeech.Result.SUCCESS
    result.accepted = False
    result.error = "rejected"
    result.artifact_identity = ""
    with pytest.raises(ValueError, match="rejected"):
        validate_run_speech_result(
            result,
            expected_identity=IDENTITY,
            expected_responder_incarnation="session-1",
        )


def test_run_speech_feedback_validates_context_enum_and_count_order() -> None:
    feedback = RunSpeech.Feedback()
    feedback.header = pcm_packet_to_msg(
        PcmPacket(
            StreamHeader(IDENTITY, 0, 1_000_000_000, "session-1"),
            "clause-0",
            0,
            0,
            16_000,
            (0.0,),
            True,
            _clause(),
            True,
            True,
        )
    ).header
    feedback.committed_clauses = 1
    feedback.generated_samples = 800
    feedback.submitted_samples = 600
    feedback.played_samples = 400
    feedback.playback_state = RunSpeech.Feedback.PLAYING

    value = validate_run_speech_feedback(
        feedback,
        expected_identity=IDENTITY,
        expected_publisher_incarnation="session-1",
    )
    assert value.played_samples == 400
    feedback.header.publisher_incarnation = "session-restarted"
    with pytest.raises(ValueError, match="publisher"):
        validate_run_speech_feedback(
            feedback,
            expected_identity=IDENTITY,
            expected_publisher_incarnation="session-1",
        )
    feedback.header.publisher_incarnation = "session-1"
    feedback.played_samples = 601
    with pytest.raises(ValueError, match="counts"):
        validate_run_speech_feedback(
            feedback,
            expected_identity=IDENTITY,
            expected_publisher_incarnation="session-1",
        )
    feedback.played_samples = 400
    feedback.playback_state = 99
    with pytest.raises(ValueError, match="state"):
        validate_run_speech_feedback(
            feedback,
            expected_identity=IDENTITY,
            expected_publisher_incarnation="session-1",
        )


def test_run_health_validates_schema_state_and_header() -> None:
    message = RunHealth()
    message.header = pcm_packet_to_msg(
        PcmPacket(HEADER, "clause-0", 0, 0, 16_000, (0.0,), True, _clause(), True, True)
    ).header
    message.schema_version = "run-health/v1"
    message.state = RunHealth.ACTIVE
    message.progress = 20
    message.detail = "playing"

    assert validate_run_health(message).state == "active"
    message.schema_version = "run-health/v9"
    with pytest.raises(ValueError, match="schema"):
        validate_run_health(message)


def test_playback_drain_requires_final_equal_counts_and_no_error() -> None:
    message = PlaybackStatus()
    message.header = pcm_packet_to_msg(
        PcmPacket(HEADER, "clause-0", 0, 0, 16_000, (0.0,), True, _clause(), True, True)
    ).header
    message.schema_version = "playback-status/v1"
    message.state = PlaybackStatus.DRAINED
    message.submitted_samples = 800
    message.played_samples = 800
    message.response_final_seen = True
    message.drained = True
    message.error = ""
    assert validate_playback_status(message).state == "drained"

    message.played_samples = 799
    with pytest.raises(ValueError, match="drain"):
        validate_playback_status(message)
    message.played_samples = 800
    message.error = "underflow"
    with pytest.raises(ValueError, match="drain"):
        validate_playback_status(message)


def test_servo_receipt_checks_selected_mapping_calibration_and_pwm_range() -> None:
    message = ServoReceipt()
    message.header = pcm_packet_to_msg(
        PcmPacket(HEADER, "clause-0", 0, 0, 16_000, (0.0,), True, _clause(), True, True)
    ).header
    message.schema_version = "servo-receipt/v1"
    message.request_id = "request-1"
    message.hardware_id = "alice-face-v1"
    message.calibration_sha256 = HASH
    message.state = ServoReceipt.APPLIED
    message.channels = [
        ServoChannelReceipt(
            actuator_name="mouth_open",
            channel=6,
            target_qus=5_000,
            observed_qus=5_000,
        )
    ]
    assert (
        validate_servo_receipt(message, expected_calibration_sha256=HASH)
        .channels[0]
        .channel
        == 6
    )

    message.channels[0].actuator_name = "upper_eyelids"
    with pytest.raises(ValueError, match="channel"):
        validate_servo_receipt(message, expected_calibration_sha256=HASH)
    message.channels[0].actuator_name = "mouth_open"
    message.channels[0].target_qus = 20_000
    with pytest.raises(ValueError, match="PWM"):
        validate_servo_receipt(message, expected_calibration_sha256=HASH)


def _begin_reply() -> BeginRun.Response:
    return BeginRun.Response(
        identity=run_identity_to_msg(IDENTITY),
        accepted=True,
        responder_node_name="tts",
        responder_incarnation="tts-1",
        clock_verified=True,
        lifecycle_state=BeginRun.Response.PREPARED,
    )


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"accepted": False}, "requires an error"),
        ({"accepted": False, "error": "rejected", "idempotent": True}, "idempotent"),
        ({"lifecycle_state": 99}, "lifecycle"),
        ({"responder_node_name": "audio"}, "responder"),
        ({"accepted": False, "error": "   "}, "error"),
    ],
)
def test_begin_reply_rejects_invalid_semantics(changes, match) -> None:
    response = _begin_reply()
    for field, value in changes.items():
        setattr(response, field, value)
    with pytest.raises(ValueError, match=match):
        validate_begin_run_response(
            response,
            expected_identity=IDENTITY,
            expected_responder=PeerIdentity("tts", "tts-1"),
            requested_operation="prepare",
            hardware=True,
        )


@pytest.mark.parametrize("accepted", [True, False])
def test_begin_reply_accepts_retry_or_explained_rejection(accepted) -> None:
    response = _begin_reply()
    response.accepted = accepted
    response.idempotent = accepted
    response.error = "" if accepted else "configuration rejected"
    value = validate_begin_run_response(
        response,
        expected_identity=IDENTITY,
        expected_responder=PeerIdentity("tts", "tts-1"),
        requested_operation="prepare",
        hardware=True,
    )
    assert value.accepted is accepted
    assert value.idempotent is accepted


def _speech_result() -> RunSpeech.Result:
    return RunSpeech.Result(
        identity=run_identity_to_msg(IDENTITY),
        accepted=True,
        terminal_outcome=RunSpeech.Result.SUCCESS,
        artifact_identity="manifest-sha256",
        responder_incarnation="session-1",
    )


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        (
            {"identity": run_identity_to_msg(RunIdentity("other", "epoch-1", "gen-1"))},
            "identity",
        ),
        ({"responder_incarnation": "session-restarted"}, "responder"),
        ({"error": "unexpected"}, "error"),
        ({"artifact_identity": "   "}, "artifact"),
        ({"terminal_outcome": RunSpeech.Result.FAULT}, "detail"),
        ({"terminal_outcome": RunSpeech.Result.CANCELLED}, "detail"),
        ({"terminal_outcome": RunSpeech.Result.FAULT, "error": "   "}, "error"),
        (
            {
                "accepted": False,
                "terminal_outcome": RunSpeech.Result.FAULT,
                "error": "rejected",
            },
            "rejected",
        ),
    ],
)
def test_run_speech_result_rejects_invalid_semantics(changes, match) -> None:
    result = _speech_result()
    for field, value in changes.items():
        setattr(result, field, value)
    with pytest.raises(ValueError, match=match):
        validate_run_speech_result(
            result,
            expected_identity=IDENTITY,
            expected_responder_incarnation="session-1",
        )


@pytest.mark.parametrize(
    ("accepted", "outcome"),
    [
        (True, RunSpeech.Result.FAULT),
        (True, RunSpeech.Result.CANCELLED),
        (False, RunSpeech.Result.FAULT),
    ],
)
def test_run_speech_result_accepts_explained_unsuccessful_outcomes(
    accepted, outcome
) -> None:
    result = _speech_result()
    result.accepted = accepted
    result.terminal_outcome = outcome
    result.error = "stopped"
    result.artifact_identity = ""
    value = validate_run_speech_result(
        result,
        expected_identity=IDENTITY,
        expected_responder_incarnation="session-1",
    )
    assert value.accepted is accepted
    assert value.error == "stopped"
    assert value.artifact_identity is None


def _speech_feedback() -> RunSpeech.Feedback:
    result = RunSpeech.Feedback()
    result.header.identity = run_identity_to_msg(IDENTITY)
    result.header.publisher_incarnation = "session-1"
    result.header.source_monotonic_ns = 1_000_000_000
    result.committed_clauses = 32
    # These cumulative audio counts include both TTS PCM and the local tail.
    result.generated_samples = 1_600
    result.submitted_samples = 1_600
    result.played_samples = 1_600
    result.playback_state = RunSpeech.Feedback.DRAINED
    return result


@pytest.mark.parametrize("fault", ["identity", "clause_bound", "generated", "drain"])
def test_run_speech_feedback_rejects_invalid_semantics(fault) -> None:
    feedback = _speech_feedback()
    if fault == "identity":
        feedback.header.identity = run_identity_to_msg(
            RunIdentity("other", "epoch-1", "gen-1")
        )
    elif fault == "clause_bound":
        feedback.committed_clauses = 33
    elif fault == "generated":
        feedback.generated_samples = 1_599
    else:
        feedback.played_samples = 1_599
    with pytest.raises(ValueError):
        validate_run_speech_feedback(
            feedback,
            expected_identity=IDENTITY,
            expected_publisher_incarnation="session-1",
        )


def test_run_speech_feedback_includes_local_tail_and_is_frozen() -> None:
    value = validate_run_speech_feedback(
        _speech_feedback(),
        expected_identity=IDENTITY,
        expected_publisher_incarnation="session-1",
    )
    assert (
        value.generated_samples
        == value.submitted_samples
        == value.played_samples
        == 1_600
    )
    assert value.committed_clauses == 32
    assert value.playback_state == "drained"
    with pytest.raises(FrozenInstanceError):
        value.generated_samples = 0  # type: ignore[misc]
