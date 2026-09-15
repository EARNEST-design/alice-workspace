from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from alice_nodes.transport import (
    CreditLedger,
    PcmPacket,
    PcmStreamGuard,
    PeerIdentity,
    RunBinding,
    RunGuard,
    RunIdentity,
    RunLifecycleGuard,
    SequenceGuard,
    StreamHeader,
)

from alice.contracts.speech_stream import SpeechClause

IDENTITY = RunIdentity(run_id="run-1", epoch="epoch-1", generation_id="gen-1")
PEERS = tuple(
    PeerIdentity(name, f"{name}-1")
    for name in (
        "session",
        "tts",
        "audio",
        "expression",
        "motion",
        "maestro",
        "perception",
        "recorder",
    )
)


def _header(sequence: int, source_ns: int = 1_000_000_000) -> StreamHeader:
    return StreamHeader(
        identity=IDENTITY,
        sequence=sequence,
        source_monotonic_ns=source_ns,
        publisher_incarnation="tts-1",
    )


def _clause(sequence: int = 0, *, final: bool = False) -> SpeechClause:
    return SpeechClause(
        schema_version="speech-clause/v1",
        generation_id="gen-1",
        clause_id=f"clause-{sequence}",
        sequence=sequence,
        text="Hello",
        vector=(0.1, -0.2, 0.3),
        intensity=0.4,
        seed=7,
        end_of_response=final,
    )


def test_run_identity_is_immutable() -> None:
    with pytest.raises(FrozenInstanceError):
        IDENTITY.epoch = "replacement"  # type: ignore[misc]


def test_terminal_fault_cannot_be_completed() -> None:
    guard = RunGuard(IDENTITY)
    guard.fail("lost playback")

    with pytest.raises(ValueError, match="terminal"):
        guard.complete()


def test_matching_terminal_result_is_idempotent() -> None:
    guard = RunGuard(IDENTITY)

    assert guard.cancel("operator") is True
    assert guard.cancel("operator") is False


def test_start_requires_matching_prepare_and_is_idempotent() -> None:
    binding = RunBinding(
        identity=IDENTITY,
        selected_profile="bench",
        seed=7,
        hardware=True,
        sad_hold_ms=2_000,
        config_sha256="a" * 64,
        calibration_sha256="b" * 64,
        clock_domain_fingerprint="salted-proof",
        requester_incarnation="session-1",
        peers=(),
    )
    guard = RunLifecycleGuard()

    with pytest.raises(ValueError, match="prepare"):
        guard.start(binding)
    assert guard.prepare(binding) is True
    assert guard.prepare(binding) is False
    started = RunBinding(
        identity=IDENTITY,
        selected_profile="bench",
        seed=7,
        hardware=True,
        sad_hold_ms=2_000,
        config_sha256="a" * 64,
        calibration_sha256="b" * 64,
        clock_domain_fingerprint="salted-proof",
        requester_incarnation="session-1",
        peers=PEERS,
    )
    assert guard.start(started) is True
    assert guard.start(started) is False


def test_prepare_cannot_be_rebound_to_changed_peer_or_config() -> None:
    binding = RunBinding(
        identity=IDENTITY,
        selected_profile="bench",
        seed=7,
        hardware=False,
        sad_hold_ms=0,
        config_sha256="a" * 64,
        calibration_sha256="b" * 64,
        clock_domain_fingerprint="salted-proof",
        requester_incarnation="session-1",
        peers=(),
    )
    guard = RunLifecycleGuard()
    guard.prepare(binding)

    with pytest.raises(ValueError, match="binding"):
        guard.prepare(
            RunBinding(
                identity=IDENTITY,
                selected_profile="bench",
                seed=7,
                hardware=False,
                sad_hold_ms=0,
                config_sha256="c" * 64,
                calibration_sha256="b" * 64,
                clock_domain_fingerprint="salted-proof",
                requester_incarnation="session-2",
                peers=(),
            )
        )


def test_start_rejects_mutated_prepare_parameters() -> None:
    prepared = RunBinding(
        identity=IDENTITY,
        selected_profile="bench",
        seed=7,
        hardware=True,
        sad_hold_ms=0,
        config_sha256="a" * 64,
        calibration_sha256="b" * 64,
        clock_domain_fingerprint="salted-proof",
        requester_incarnation="session-1",
        peers=(),
    )
    guard = RunLifecycleGuard()
    guard.prepare(prepared)
    changed = RunBinding(
        identity=IDENTITY,
        selected_profile="bench",
        seed=8,
        hardware=True,
        sad_hold_ms=0,
        config_sha256="a" * 64,
        calibration_sha256="b" * 64,
        clock_domain_fingerprint="salted-proof",
        requester_incarnation="session-1",
        peers=PEERS,
    )

    with pytest.raises(ValueError, match="binding"):
        guard.start(changed)


def test_stale_epoch_is_ignored_without_replacing_active_identity() -> None:
    guard = RunGuard(IDENTITY)
    stale = RunIdentity("run-1", "epoch-old", "gen-old")

    assert guard.admit(stale) is False
    assert guard.identity == IDENTITY


def test_exact_sequence_rejection_does_not_consume_sequence() -> None:
    guard = SequenceGuard(IDENTITY, exact=True)

    with pytest.raises(ValueError, match="expired"):
        guard.admit(_header(0, 700_000_000), now_monotonic_ns=1_000_000_001)

    assert guard.admit(_header(0), now_monotonic_ns=1_000_000_000)
    with pytest.raises(ValueError, match="sequence"):
        guard.admit(_header(0), now_monotonic_ns=1_000_000_000)


def test_future_timestamp_and_progress_gap_are_rejected_transactionally() -> None:
    guard = SequenceGuard(IDENTITY, exact=True)

    with pytest.raises(ValueError, match="future"):
        guard.admit(_header(0, 1_000_000_001), now_monotonic_ns=1_000_000_000)
    assert guard.admit(_header(0), now_monotonic_ns=1_000_000_000)
    with pytest.raises(ValueError, match="progress gap"):
        guard.admit(_header(1, 1_250_000_001), now_monotonic_ns=1_250_000_001)
    assert guard.admit(_header(1, 1_250_000_000), now_monotonic_ns=1_250_000_000)


def test_latest_only_stream_allows_skips_but_not_duplicates_or_backwards() -> None:
    guard = SequenceGuard(IDENTITY, exact=False)

    assert guard.admit(_header(3), now_monotonic_ns=1_000_000_000)
    assert guard.admit(_header(8, 1_010_000_000), now_monotonic_ns=1_010_000_000)
    with pytest.raises(ValueError, match="sequence"):
        guard.admit(_header(8, 1_020_000_000), now_monotonic_ns=1_020_000_000)


def test_restarted_publisher_cannot_continue_prior_stream_sequence() -> None:
    guard = SequenceGuard(IDENTITY, exact=True)
    assert guard.admit(_header(0), now_monotonic_ns=1_000_000_000)
    restarted = StreamHeader(
        identity=IDENTITY,
        sequence=1,
        source_monotonic_ns=1_010_000_000,
        publisher_incarnation="tts-2",
    )

    with pytest.raises(ValueError, match="incarnation"):
        guard.admit(restarted, now_monotonic_ns=1_010_000_000)

    assert guard.admit(
        _header(1, 1_010_000_000), now_monotonic_ns=1_010_000_000
    )


def test_coalesced_latest_stream_skips_sequences_within_time_gap_bound() -> None:
    guard = SequenceGuard(IDENTITY, exact=False)

    assert guard.admit(_header(0), now_monotonic_ns=1_000_000_000)
    assert guard.admit(
        _header(5, 1_200_000_000), now_monotonic_ns=1_200_000_000
    )
    with pytest.raises(ValueError, match="progress gap"):
        guard.admit(
            _header(9, 1_450_000_001), now_monotonic_ns=1_450_000_001
        )
    assert guard.admit(
        _header(9, 1_450_000_000), now_monotonic_ns=1_450_000_000
    )


def test_credit_is_cumulative_and_duplicate_cannot_enlarge_window() -> None:
    ledger = CreditLedger(IDENTITY, capacity_samples=200)

    ledger.reserve(150)
    assert ledger.available_samples == 50
    assert ledger.acknowledge(IDENTITY, cumulative_consumed_samples=100)
    assert ledger.available_samples == 150
    assert ledger.acknowledge(IDENTITY, cumulative_consumed_samples=100) is False
    assert ledger.available_samples == 150
    ledger.reserve(150)
    with pytest.raises(ValueError, match="capacity"):
        ledger.reserve(1)


def test_credit_cannot_ack_unsent_samples_or_replenish_wrong_run() -> None:
    ledger = CreditLedger(IDENTITY, capacity_samples=200)
    ledger.reserve(50)

    with pytest.raises(ValueError, match="sent"):
        ledger.acknowledge(IDENTITY, cumulative_consumed_samples=51)
    wrong = RunIdentity("other", "epoch-1", "gen-1")
    assert ledger.acknowledge(wrong, cumulative_consumed_samples=50) is False
    assert ledger.available_samples == 150


def test_malformed_pcm_does_not_consume_sequence_or_offset() -> None:
    guard = PcmStreamGuard(IDENTITY)
    malformed = PcmPacket(
        header=_header(0),
        clause_id="clause-0",
        clause_sequence=0,
        global_sample_offset=0,
        sample_rate=16_000,
        samples=(0.0, float("nan")),
        first_packet=True,
        clause=_clause(final=True),
        clause_final=True,
        response_final=True,
    )

    with pytest.raises(ValueError, match="finite"):
        guard.admit(malformed, now_monotonic_ns=1_000_000_000)

    valid = PcmPacket(
        header=_header(0),
        clause_id="clause-0",
        clause_sequence=0,
        global_sample_offset=0,
        sample_rate=16_000,
        samples=(0.0, 0.25),
        first_packet=True,
        clause=_clause(final=True),
        clause_final=True,
        response_final=True,
    )
    assert guard.admit(valid, now_monotonic_ns=1_000_000_000) == 2


def test_pcm_requires_complete_clause_metadata_on_first_packet() -> None:
    guard = PcmStreamGuard(IDENTITY)
    packet = PcmPacket(
        header=_header(0),
        clause_id="clause-0",
        clause_sequence=0,
        global_sample_offset=0,
        sample_rate=16_000,
        samples=(0.0,),
        first_packet=False,
        clause=None,
        clause_final=False,
        response_final=False,
    )

    with pytest.raises(ValueError, match="first packet"):
        guard.admit(packet, now_monotonic_ns=1_000_000_000)


def test_pcm_clause_and_response_final_markers_must_agree() -> None:
    guard = PcmStreamGuard(IDENTITY)
    packet = PcmPacket(
        header=_header(0),
        clause_id="clause-0",
        clause_sequence=0,
        global_sample_offset=0,
        sample_rate=16_000,
        samples=(),
        first_packet=True,
        clause=_clause(final=True),
        clause_final=True,
        response_final=False,
    )

    with pytest.raises(ValueError, match="response-final"):
        guard.admit(packet, now_monotonic_ns=1_000_000_000)


def test_pcm_rejects_out_of_order_clause_without_mutating_ledger() -> None:
    guard = PcmStreamGuard(IDENTITY)
    out_of_order = PcmPacket(
        header=_header(0),
        clause_id="clause-1",
        clause_sequence=1,
        global_sample_offset=0,
        sample_rate=16_000,
        samples=(0.1,),
        first_packet=True,
        clause=_clause(1),
        clause_final=True,
        response_final=False,
    )
    with pytest.raises(ValueError, match="clause"):
        guard.admit(out_of_order, now_monotonic_ns=1_000_000_000)

    first = PcmPacket(
        header=_header(0),
        clause_id="clause-0",
        clause_sequence=0,
        global_sample_offset=0,
        sample_rate=16_000,
        samples=(0.1,),
        first_packet=True,
        clause=_clause(0),
        clause_final=True,
        response_final=False,
    )
    assert guard.admit(first, now_monotonic_ns=1_000_000_000) == 1


def test_pcm_rejects_duplicate_clause_id_without_mutating_ledgers() -> None:
    guard = PcmStreamGuard(IDENTITY)
    first_clause = _clause(0)
    first = PcmPacket(
        _header(0), "clause-0", 0, 0, 16_000, (0.1,), True, first_clause, True, False
    )
    assert guard.admit(first, now_monotonic_ns=1_000_000_000) == 1

    duplicate = SpeechClause(
        generation_id="gen-1",
        clause_id="clause-0",
        sequence=1,
        text="again",
        vector=(0.1, -0.2, 0.3),
        intensity=0.4,
        seed=8,
    )
    malformed = PcmPacket(
        _header(1), "clause-0", 1, 1, 16_000, (0.2,), True, duplicate, True, False
    )
    with pytest.raises(ValueError, match="duplicate"):
        guard.admit(malformed, now_monotonic_ns=1_000_000_000)

    replacement = _clause(1)
    retry = PcmPacket(
        _header(1), "clause-1", 1, 1, 16_000, (0.2,), True, replacement, True, False
    )
    assert guard.admit(retry, now_monotonic_ns=1_000_000_000) == 1
    assert guard.sample_offset == 2


def test_pcm_rejects_response_text_over_budget_without_mutating_ledgers() -> None:
    guard = PcmStreamGuard(IDENTITY)
    offset = 0
    for sequence, size in enumerate((1_000, 1_000, 1_000, 999)):
        clause = SpeechClause(
            generation_id="gen-1",
            clause_id=f"clause-{sequence}",
            sequence=sequence,
            text="x" * size,
            vector=(0.1, -0.2, 0.3),
            intensity=0.4,
            seed=sequence,
        )
        packet = PcmPacket(
            _header(sequence),
            clause.clause_id,
            sequence,
            offset,
            16_000,
            (0.1,),
            True,
            clause,
            True,
            False,
        )
        assert guard.admit(packet, now_monotonic_ns=1_000_000_000) == 1
        offset += 1

    over_budget = SpeechClause(
        generation_id="gen-1",
        clause_id="clause-4",
        sequence=4,
        text="xx",
        vector=(0.1, -0.2, 0.3),
        intensity=0.4,
        seed=4,
        end_of_response=True,
    )
    malformed = PcmPacket(
        _header(4), "clause-4", 4, 4, 16_000, (0.1,), True, over_budget, True, True
    )
    with pytest.raises(ValueError, match="4000"):
        guard.admit(malformed, now_monotonic_ns=1_000_000_000)

    final_clause = over_budget.model_copy(update={"text": "x"})
    retry = PcmPacket(
        _header(4), "clause-4", 4, 4, 16_000, (0.1,), True, final_clause, True, True
    )
    assert guard.admit(retry, now_monotonic_ns=1_000_000_000) == 1
    assert guard.sample_offset == 5


def test_oversize_pcm_rejection_does_not_consume_sequence() -> None:
    guard = PcmStreamGuard(IDENTITY)
    oversize = PcmPacket(
        header=_header(0),
        clause_id="clause-0",
        clause_sequence=0,
        global_sample_offset=0,
        sample_rate=8_000,
        samples=(0.0,) * 161,
        first_packet=True,
        clause=_clause(final=True),
        clause_final=True,
        response_final=True,
    )
    with pytest.raises(ValueError, match="20 ms"):
        guard.admit(oversize, now_monotonic_ns=1_000_000_000)

    valid = PcmPacket(
        header=_header(0),
        clause_id="clause-0",
        clause_sequence=0,
        global_sample_offset=0,
        sample_rate=8_000,
        samples=(0.0,) * 160,
        first_packet=True,
        clause=_clause(final=True),
        clause_final=True,
        response_final=True,
    )
    assert guard.admit(valid, now_monotonic_ns=1_000_000_000) == 160
