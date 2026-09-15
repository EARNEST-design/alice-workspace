"""Pure, transactional run and streaming transport guards."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Literal

from alice.contracts.speech_stream import ClauseSequence, SpeechClause

MAX_TRANSPORT_AGE_NS = 250_000_000
MAX_PROGRESS_GAP_NS = 250_000_000
MAX_PCM_SAMPLES = 3_840
RUNTIME_NODE_NAMES = frozenset(
    {
        "session",
        "tts",
        "audio",
        "expression",
        "motion",
        "maestro",
        "perception",
        "recorder",
    }
)


def _bounded_text(value: str, name: str, maximum: int = 128) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"invalid {name}")


@dataclass(frozen=True)
class RunIdentity:
    run_id: str
    epoch: str
    generation_id: str

    def __post_init__(self) -> None:
        _bounded_text(self.run_id, "run ID")
        _bounded_text(self.epoch, "epoch")
        _bounded_text(self.generation_id, "generation ID")


@dataclass(frozen=True)
class StreamHeader:
    identity: RunIdentity
    sequence: int
    source_monotonic_ns: int
    publisher_incarnation: str

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or not 0 <= self.sequence < 2**64:
            raise ValueError("invalid stream sequence")
        if (
            type(self.source_monotonic_ns) is not int
            or not 0 <= self.source_monotonic_ns < 2**64
        ):
            raise ValueError("invalid source monotonic timestamp")
        _bounded_text(self.publisher_incarnation, "publisher incarnation")


class RunGuard:
    """Own one immutable run identity and latch its first terminal outcome."""

    def __init__(
        self,
        identity: RunIdentity | None = None,
        *,
        run_id: str | None = None,
        epoch: str | None = None,
        generation_id: str | None = None,
    ) -> None:
        if identity is None:
            if run_id is None or epoch is None or generation_id is None:
                raise ValueError("complete run identity is required")
            identity = RunIdentity(run_id, epoch, generation_id)
        elif any(value is not None for value in (run_id, epoch, generation_id)):
            raise ValueError("provide one run identity representation")
        self._identity = identity
        self._terminal: Literal["completed", "cancelled", "failed"] | None = None
        self._detail: str | None = None

    @property
    def identity(self) -> RunIdentity:
        return self._identity

    @property
    def terminal(self) -> str | None:
        return self._terminal

    @property
    def detail(self) -> str | None:
        return self._detail

    def admit(self, identity: RunIdentity) -> bool:
        """Return false for stale/replaced identities; never adopt them."""

        return identity == self._identity

    def complete(self) -> bool:
        return self._set_terminal("completed", "completed")

    def cancel(self, detail: str) -> bool:
        return self._set_terminal("cancelled", detail)

    def fail(self, detail: str) -> bool:
        return self._set_terminal("failed", detail)

    def _set_terminal(
        self, outcome: Literal["completed", "cancelled", "failed"], detail: str
    ) -> bool:
        _bounded_text(detail, "terminal detail", 256)
        if self._terminal is None:
            self._terminal = outcome
            self._detail = detail
            return True
        if self._terminal == outcome and self._detail == detail:
            return False
        raise ValueError("run already has a conflicting terminal outcome")


@dataclass(frozen=True)
class PeerIdentity:
    node_name: str
    incarnation: str

    def __post_init__(self) -> None:
        _bounded_text(self.node_name, "peer node name", 32)
        _bounded_text(self.incarnation, "peer incarnation", 128)


@dataclass(frozen=True)
class RunBinding:
    """Immutable configuration and peer identity agreed during PREPARE."""

    identity: RunIdentity
    selected_profile: str
    seed: int
    hardware: bool
    sad_hold_ms: int
    config_sha256: str
    calibration_sha256: str
    clock_domain_fingerprint: str
    requester_incarnation: str
    peers: tuple[PeerIdentity, ...]

    def __post_init__(self) -> None:
        _bounded_text(self.selected_profile, "selected profile", 64)
        if type(self.seed) is not int or not 0 <= self.seed < 2**32:
            raise ValueError("invalid run seed")
        if type(self.hardware) is not bool:
            raise ValueError("hardware flag must be boolean")
        if type(self.sad_hold_ms) is not int or not 0 <= self.sad_hold_ms <= 2_000:
            raise ValueError("invalid sad hold")
        if re.fullmatch(r"[0-9a-f]{64}", self.config_sha256) is None:
            raise ValueError("invalid config hash")
        if re.fullmatch(r"[0-9a-f]{64}", self.calibration_sha256) is None:
            raise ValueError("invalid calibration hash")
        _bounded_text(self.clock_domain_fingerprint, "clock-domain fingerprint", 128)
        _bounded_text(self.requester_incarnation, "requester incarnation", 128)
        names = [peer.node_name for peer in self.peers]
        if len(names) != len(set(names)):
            raise ValueError("peer roster has duplicate node names")
        if self.peers and set(names) != RUNTIME_NODE_NAMES:
            raise ValueError("peer roster must identify all eight runtime nodes")


class RunLifecycleGuard:
    """Enforce PREPARE before START and make exact retries idempotent."""

    def __init__(self) -> None:
        self._binding: RunBinding | None = None
        self._active = False

    @property
    def binding(self) -> RunBinding | None:
        return self._binding

    @property
    def state(self) -> Literal["new", "prepared", "active"]:
        if self._binding is None:
            return "new"
        return "active" if self._active else "prepared"

    def prepare(self, binding: RunBinding) -> bool:
        if binding.peers:
            raise ValueError("prepare must not supply a peer roster")
        if self._binding is None:
            self._binding = binding
            return True
        if replace(self._binding, peers=()) == binding:
            return False
        raise ValueError("run preparation cannot replace its immutable binding")

    def start(self, binding: RunBinding) -> bool:
        if self._binding is None:
            raise ValueError("run must prepare before start")
        if not binding.peers:
            raise ValueError("start requires the prepared peer roster")
        if self._active:
            if self._binding == binding:
                return False
            raise ValueError("run start does not match active peer binding")
        if replace(self._binding, peers=()) != replace(binding, peers=()):
            raise ValueError("run start does not match prepared binding")
        self._binding = binding
        self._active = True
        return True


class SequenceGuard:
    """Validate stream age/order first and mutate the ledger only on commit."""

    def __init__(
        self,
        identity: RunIdentity,
        *,
        exact: bool,
        max_age_ns: int = MAX_TRANSPORT_AGE_NS,
        max_gap_ns: int | None = MAX_PROGRESS_GAP_NS,
    ) -> None:
        if type(exact) is not bool:
            raise ValueError("exact must be boolean")
        if not 0 < max_age_ns <= MAX_TRANSPORT_AGE_NS:
            raise ValueError("invalid maximum transport age")
        if max_gap_ns is not None and not 0 < max_gap_ns <= MAX_PROGRESS_GAP_NS:
            raise ValueError("invalid maximum progress gap")
        self.identity = identity
        self.exact = exact
        self.max_age_ns = max_age_ns
        self.max_gap_ns = max_gap_ns
        self._last_sequence: int | None = None
        self._last_source_ns: int | None = None
        self._publisher_incarnation: str | None = None

    def validate(self, header: StreamHeader, *, now_monotonic_ns: int) -> bool:
        if header.identity != self.identity:
            return False
        if type(now_monotonic_ns) is not int or now_monotonic_ns < 0:
            raise ValueError("invalid receiver monotonic timestamp")
        if header.source_monotonic_ns > now_monotonic_ns:
            raise ValueError("source timestamp is in the future")
        if now_monotonic_ns - header.source_monotonic_ns > self.max_age_ns:
            raise ValueError("stream sample is expired")
        if self._last_sequence is None:
            if self.exact and header.sequence != 0:
                raise ValueError("exact stream sequence must start at zero")
            return True
        expected = self._last_sequence + 1
        if header.publisher_incarnation != self._publisher_incarnation:
            raise ValueError("publisher incarnation changed within stream")
        if self.exact and header.sequence != expected:
            raise ValueError("missing, duplicate or out-of-order stream sequence")
        if not self.exact and header.sequence <= self._last_sequence:
            raise ValueError("duplicate or backward stream sequence")
        assert self._last_source_ns is not None
        if header.source_monotonic_ns < self._last_source_ns:
            raise ValueError("source timestamps moved backward")
        if (
            self.max_gap_ns is not None
            and header.source_monotonic_ns - self._last_source_ns > self.max_gap_ns
        ):
            raise ValueError("stream progress gap exceeds limit")
        return True

    def commit(self, header: StreamHeader) -> None:
        self._last_sequence = header.sequence
        self._last_source_ns = header.source_monotonic_ns
        self._publisher_incarnation = header.publisher_incarnation

    def admit(self, header: StreamHeader, *, now_monotonic_ns: int) -> bool:
        if not self.validate(header, now_monotonic_ns=now_monotonic_ns):
            return False
        self.commit(header)
        return True


class CreditLedger:
    """Generation-scoped cumulative credit including all sent/in-flight PCM."""

    def __init__(self, identity: RunIdentity, *, capacity_samples: int) -> None:
        if type(capacity_samples) is not int or not 1 <= capacity_samples <= 384_000:
            raise ValueError("invalid PCM credit capacity")
        self.identity = identity
        self.capacity_samples = capacity_samples
        self.sent_samples = 0
        self.cumulative_consumed_samples = 0

    @property
    def available_samples(self) -> int:
        return self.capacity_samples - (
            self.sent_samples - self.cumulative_consumed_samples
        )

    def reserve(self, sample_count: int) -> None:
        if type(sample_count) is not int or sample_count < 0:
            raise ValueError("invalid sample reservation")
        if sample_count > self.available_samples:
            raise ValueError("PCM reservation exceeds credited capacity")
        self.sent_samples += sample_count

    def acknowledge(
        self, identity: RunIdentity, *, cumulative_consumed_samples: int
    ) -> bool:
        if identity != self.identity:
            return False
        if (
            type(cumulative_consumed_samples) is not int
            or cumulative_consumed_samples < 0
        ):
            raise ValueError("invalid cumulative acknowledgement")
        if cumulative_consumed_samples > self.sent_samples:
            raise ValueError("acknowledgement exceeds samples sent")
        if cumulative_consumed_samples < self.cumulative_consumed_samples:
            raise ValueError("cumulative acknowledgement moved backward")
        if cumulative_consumed_samples == self.cumulative_consumed_samples:
            return False
        self.cumulative_consumed_samples = cumulative_consumed_samples
        return True


@dataclass(frozen=True)
class PcmPacket:
    header: StreamHeader
    clause_id: str
    clause_sequence: int
    global_sample_offset: int
    sample_rate: int
    samples: tuple[float, ...]
    first_packet: bool
    clause: SpeechClause | None
    clause_final: bool
    response_final: bool


class PcmStreamGuard:
    """Admit complete, exact-order PCM packets with transactional validation."""

    def __init__(self, identity: RunIdentity) -> None:
        self.identity = identity
        self._sequences = SequenceGuard(identity, exact=True)
        self._sample_rate: int | None = None
        self._sample_offset = 0
        self._expected_clause = 0
        self._active_clause_id: str | None = None
        self._active_clause_end = False
        self._response_finished = False
        self._clauses = ClauseSequence()

    @property
    def sample_offset(self) -> int:
        return self._sample_offset

    def admit(self, packet: PcmPacket, *, now_monotonic_ns: int) -> int | None:
        if not self._sequences.validate(
            packet.header, now_monotonic_ns=now_monotonic_ns
        ):
            return None
        self._validate_packet(packet)
        if packet.first_packet:
            assert packet.clause is not None
            self._clauses.commit(packet.clause)

        self._sequences.commit(packet.header)
        if packet.first_packet:
            assert packet.clause is not None
            self._active_clause_id = packet.clause_id
            self._active_clause_end = packet.clause.end_of_response
        self._sample_rate = packet.sample_rate
        self._sample_offset += len(packet.samples)
        if packet.clause_final:
            self._active_clause_id = None
            self._expected_clause += 1
            self._response_finished = packet.response_final
        return len(packet.samples)

    def _validate_packet(self, packet: PcmPacket) -> None:
        if self._response_finished:
            raise ValueError("PCM response is already final")
        _bounded_text(packet.clause_id, "clause ID")
        if (
            type(packet.clause_sequence) is not int
            or packet.clause_sequence < 0
            or type(packet.global_sample_offset) is not int
            or packet.global_sample_offset < 0
        ):
            raise ValueError("invalid clause sequence or sample offset")
        if packet.global_sample_offset != self._sample_offset:
            raise ValueError("PCM global sample offset is discontinuous")
        if (
            type(packet.sample_rate) is not int
            or not 8_000 <= packet.sample_rate <= 192_000
            or self._sample_rate not in (None, packet.sample_rate)
        ):
            raise ValueError("invalid or replaced PCM sample rate")
        maximum = min(MAX_PCM_SAMPLES, packet.sample_rate // 50)
        if len(packet.samples) > maximum:
            raise ValueError("PCM packet exceeds 20 ms")
        if not packet.samples and not packet.clause_final:
            raise ValueError("empty PCM is only valid as a final marker")
        if any(not math.isfinite(value) or abs(value) > 1 for value in packet.samples):
            raise ValueError("PCM samples must be finite and within [-1, 1]")
        if self._active_clause_id is None:
            if not packet.first_packet or packet.clause is None:
                raise ValueError(
                    "a clause must begin with complete first packet metadata"
                )
            clause = SpeechClause.model_validate(packet.clause)
            if (
                packet.clause_sequence != self._expected_clause
                or clause.sequence != self._expected_clause
                or clause.clause_id != packet.clause_id
                or clause.generation_id != self.identity.generation_id
            ):
                raise ValueError("first packet clause identity or ordering is invalid")
            clause_end = clause.end_of_response
        else:
            if packet.first_packet or packet.clause is not None:
                raise ValueError("subsequent PCM packet must only identify its clause")
            if (
                packet.clause_id != self._active_clause_id
                or packet.clause_sequence != self._expected_clause
            ):
                raise ValueError("PCM packet identifies the wrong active clause")
            clause_end = self._active_clause_end
        if packet.response_final and not packet.clause_final:
            raise ValueError("response-final requires clause-final")
        if packet.clause_final and packet.response_final != clause_end:
            raise ValueError("response-final marker disagrees with committed clause")
