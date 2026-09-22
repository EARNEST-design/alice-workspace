"""Thread-safe, bounded event history for the attended conversation bench."""

from __future__ import annotations

import json
import math
import time
import uuid
from collections import OrderedDict, deque
from threading import Condition
from typing import TypeVar, cast

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type Event = dict[str, JsonValue]
T = TypeVar("T")

_HISTORY_LIMIT = 256
_STAGE_LIMIT = 32
_DETAILS_BYTES_LIMIT = 16_384
_LABEL_LIMIT = 64


def _clone_json(value: T) -> T:
    return cast(T, json.loads(json.dumps(value, allow_nan=False)))


def _event_sequence(event: Event) -> int:
    value = event["sequence"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError("stored event has an invalid sequence")
    return value


def _validate_label(name: str, value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > _LABEL_LIMIT:
        raise ValueError(f"{name} must be a non-empty string of at most 64 characters")


def _json_details(details: dict[str, object]) -> dict[str, JsonValue]:
    try:
        encoded = json.dumps(
            details,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("event details must contain finite JSON values") from exc
    if len(encoded) > _DETAILS_BYTES_LIMIT:
        raise ValueError("event details exceed the 16384-byte limit")
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError("event details must encode as an object")
    return cast(dict[str, JsonValue], decoded)


class EventStore:
    """Keep the latest stage state and a replayable in-memory event window."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._session_id = uuid.uuid4().hex
        self._sequence = 0
        self._events: deque[Event] = deque(maxlen=_HISTORY_LIMIT)
        self._stages: OrderedDict[str, Event] = OrderedDict()

    def emit(self, stage: str, state: str, **details: object) -> Event:
        """Append one JSON event and wake observers waiting on its sequence."""

        _validate_label("stage", stage)
        _validate_label("state", state)
        clean_details = _json_details(details)
        with self._condition:
            self._sequence += 1
            event: Event = {
                "schema_version": "bench-event/v1",
                "session_id": self._session_id,
                "sequence": self._sequence,
                "monotonic_ns": time.monotonic_ns(),
                "stage": stage,
                "state": state,
                "details": clean_details,
            }
            self._events.append(event)
            self._stages[stage] = event
            self._stages.move_to_end(stage)
            while len(self._stages) > _STAGE_LIMIT:
                self._stages.popitem(last=False)
            self._condition.notify_all()
            return _clone_json(event)

    def snapshot(self) -> dict[str, JsonValue]:
        """Return a detached, internally consistent view of current state."""

        with self._condition:
            snapshot: dict[str, JsonValue] = {
                "session_id": self._session_id,
                "sequence": self._sequence,
                "stages": dict(self._stages),
                "events": list(self._events),
            }
            return _clone_json(snapshot)

    def since(self, after: int) -> list[Event]:
        """Return retained events with sequence numbers greater than ``after``."""

        self._validate_cursor(after)
        with self._condition:
            return self._since_locked(after)

    def wait(self, after: int, timeout: float = 1) -> list[Event]:
        """Wait up to ``timeout`` seconds for an event newer than ``after``."""

        self._validate_cursor(after)
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout):
            raise ValueError("timeout must be a finite non-negative number")
        if timeout < 0:
            raise ValueError("timeout must be a finite non-negative number")
        with self._condition:
            self._condition.wait_for(
                lambda: self._sequence > after,
                timeout=float(timeout),
            )
            return self._since_locked(after)

    def _replay(
        self,
        after: int,
        *,
        session_id: str | None = None,
    ) -> tuple[bool, list[Event]]:
        """Atomically report cursor expiry and return retained replay events."""

        self._validate_cursor(after)
        with self._condition:
            oldest = _event_sequence(self._events[0]) if self._events else 0
            expired = (
                (session_id is not None and session_id != self._session_id)
                or after > self._sequence
                or (bool(self._events) and after < oldest - 1)
            )
            return expired, self._since_locked(after)

    def _heartbeat(self) -> Event:
        """Return current stream identity without adding an event to history."""

        with self._condition:
            return {
                "schema_version": "bench-event/v1",
                "session_id": self._session_id,
                "sequence": self._sequence,
                "monotonic_ns": time.monotonic_ns(),
                "stage": "session",
                "state": "heartbeat",
                "details": {},
            }

    def _since_locked(self, after: int) -> list[Event]:
        events = [event for event in self._events if _event_sequence(event) > after]
        return _clone_json(events)

    @staticmethod
    def _validate_cursor(after: int) -> None:
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ValueError("event cursor must be a non-negative integer")
