"""Provider-independent, immutable committed clauses for one bounded response."""

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from alice.contracts.affect import UnitIntervalFloat
from alice.contracts.speech import Text, Vector


class SpeechClause(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["speech-clause/v1"] = "speech-clause/v1"
    generation_id: Text
    clause_id: Text
    sequence: int = Field(ge=0, le=31, strict=True)
    text: Text
    vector: Vector
    intensity: UnitIntervalFloat
    seed: int = Field(ge=0, le=2**32 - 1, strict=True)
    end_of_response: bool = False


class ClauseSequence:
    """Validate before admitting work; a rejected event never mutates the ledger."""

    def __init__(self) -> None:
        self.generation_id: str | None = None
        self._ids: set[str] = set()
        self._characters = 0
        self._ended = False

    def commit(self, clause: SpeechClause) -> None:
        if self._ended:
            raise ValueError("response already committed end_of_response")
        if self.generation_id not in (None, clause.generation_id):
            raise ValueError("generation changed within response")
        if clause.sequence != len(self._ids) or clause.clause_id in self._ids:
            raise ValueError("duplicate, replaced or out-of-order committed clause")
        if self._characters + len(clause.text) > 4000:
            raise ValueError("response text exceeds 4000 characters")
        self.generation_id = clause.generation_id
        self._ids.add(clause.clause_id)
        self._characters += len(clause.text)
        self._ended = clause.end_of_response

    def finish(self) -> None:
        if not self._ended:
            raise ValueError("source closed without end_of_response")


async def jsonl_clauses(path: Path) -> AsyncGenerator[SpeechClause, None]:
    """Read a local fixture on demand; no whole-response JSON or eager parsing."""
    with path.open() as source:
        for line in source:
            yield SpeechClause.model_validate_json(line)
