"""Committed text cannot be replaced, reordered, or silently change generation."""

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from alice.contracts.speech_stream import ClauseSequence, SpeechClause, jsonl_clauses


def clause(**overrides):
    return SpeechClause.model_validate(
        {
            "generation_id": "g1",
            "clause_id": "c1",
            "sequence": 0,
            "text": "Hello.",
            "vector": [0.5, 0.2, 0],
            "intensity": 0.7,
            "seed": 29,
            **overrides,
        }
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"text": "  "},
        {"text": "x" * 1001},
        {"vector": [float("nan"), 0, 0]},
        {"vector": [1.1, 0, 0]},
        {"intensity": float("inf")},
        {"intensity": -0.1},
        {"seed": -1},
        {"seed": 2**32},
        {"seed": True},
        {"sequence": -1},
        {"generation_id": " "},
        {"clause_id": ""},
    ],
)
def test_invalid_clause_rejected(overrides):
    with pytest.raises(ValidationError):
        clause(**overrides)


def test_commits_are_immutable_and_sequence_is_bounded():
    first = clause()
    with pytest.raises(ValidationError):
        first.text = "replacement"
    ledger = ClauseSequence()
    ledger.commit(first)
    for invalid in (
        first,
        clause(text="replacement"),
        clause(sequence=1),
        clause(clause_id="c2", sequence=2),
        clause(clause_id="c2", sequence=1, generation_id="g2"),
    ):
        with pytest.raises(ValueError):
            ledger.commit(invalid)
    ledger.commit(clause(clause_id="c2", sequence=1, end_of_response=True))
    ledger.finish()
    with pytest.raises(ValueError):
        ledger.commit(clause(clause_id="c3", sequence=2))


def test_truncated_response_is_not_success():
    ledger = ClauseSequence()
    ledger.commit(clause())
    with pytest.raises(ValueError, match="end_of_response"):
        ledger.finish()


def test_jsonl_source_requests_only_one_clause_at_a_time(tmp_path):
    async def check():
        fixture = tmp_path / "clauses.jsonl"
        fixture.write_text(clause().model_dump_json() + "\ninvalid second line\n")
        source = jsonl_clauses(fixture)
        assert (await anext(source)).clause_id == "c1"
        with pytest.raises(ValidationError):
            await anext(source)
        source = jsonl_clauses(Path("config/speech/stream-demo-v1.jsonl"))
        first, second = await anext(source), await anext(source)
        assert first.vector[0] > 0 > second.vector[0]
        assert second.end_of_response
        await source.aclose()

    asyncio.run(check())
