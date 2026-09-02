from __future__ import annotations

import hashlib
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from alice.experiments.hardware_identification import ElectricalSafetyEvidence


def test_alice_servo_supply_evidence_matches_reviewed_source() -> None:
    evidence_path = Path("hardware/electrical/alice-servo-supply-6v-1a.yaml")
    evidence = ElectricalSafetyEvidence.model_validate(
        yaml.safe_load(evidence_path.read_text(encoding="utf-8"))
    )
    source_path = Path(evidence.source)

    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == (
        evidence.source_document_sha256
    )
    assert (evidence.supply_voltage_v, evidence.current_limit_a) == (6.0, 1.0)
    assert "single actuator" in evidence.scope
