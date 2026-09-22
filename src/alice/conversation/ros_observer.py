"""Device-free ROS observer; runnable as a standalone file inside a ROS image."""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, BinaryIO

# A reset contains at most 256 events and 32 stage snapshots, each with
# 16 KiB of UTF-8 details. JSON's ASCII escaping can expand those details 3x.
_SNAPSHOT_LINE_LIMIT = 16 * 1024 * 1024


def read_sse_line(source: BinaryIO) -> bytes:
    line = source.readline(_SNAPSHOT_LINE_LIMIT + 1)
    if len(line) > _SNAPSHOT_LINE_LIMIT:
        raise ValueError("observer SSE line exceeds limit")
    return line


def event_payload(event: dict[str, Any]) -> str:
    if (
        event.get("schema_version") != "bench-event/v1"
        or type(event.get("sequence")) is not int
        or event["sequence"] < 1
        or type(event.get("monotonic_ns")) is not int
        or event["monotonic_ns"] < 0
        or not isinstance(event.get("session_id"), str)
        or not isinstance(event.get("stage"), str)
        or not isinstance(event.get("state"), str)
        or not isinstance(event.get("details"), dict)
    ):
        raise ValueError("invalid conversation event")
    result = json.dumps(
        event, separators=(",", ":"), allow_nan=False, ensure_ascii=False
    )
    if len(result.encode()) > 20000:
        raise ValueError("conversation event exceeds ROS observer limit")
    return result


def observation_data(
    raw: bytes, event_type: str, after: int, session: str | None
) -> tuple[list[dict[str, Any]], int, str | None]:
    """Validate a complete record before publishing it or advancing the cursor."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("invalid observer record")
    if event_type == "reset":
        events = data.get("events")
        cursor = data.get("sequence")
        reset_session = data.get("session_id")
        if (
            not isinstance(events, list)
            or len(events) > 256
            or type(cursor) is not int
            or cursor < 0
            or not isinstance(reset_session, str)
            or not reset_session
        ):
            raise ValueError("invalid observer reset")
        previous = 0
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("invalid observer event")
            event_payload(event)
            if event["session_id"] != reset_session:
                raise ValueError("reset event session mismatch")
            if not previous < event["sequence"] <= cursor:
                raise ValueError("reset event sequence mismatch")
            previous = event["sequence"]
        if (events and previous != cursor) or (not events and cursor != 0):
            raise ValueError("reset cursor mismatch")
        return events, cursor, reset_session
    if event_type != "event":
        return [], after, session
    event_payload(data)
    if data["session_id"] == session and data["sequence"] <= after:
        return [], after, session
    return [data], data["sequence"], data["session_id"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    options = parser.parse_args()
    if options.url != "http://127.0.0.1:8765":
        parser.error("observer is restricted to the local bench on port 8765")
    import rclpy  # type: ignore[import-not-found]
    from std_msgs.msg import String  # type: ignore[import-not-found]

    rclpy.init()
    node = rclpy.create_node("alice_conversation_observer")
    publisher = node.create_publisher(String, "/alice/conversation/events", 50)
    stop = threading.Event()
    last_event = [0.0]

    def heartbeat() -> None:
        while not stop.wait(2):
            if time.monotonic() - last_event[0] > 4:
                continue
            request = urllib.request.Request(
                options.url + "/api/control",
                data=b'{"action":"bridge"}',
                headers={"Content-Type": "application/json", "Origin": options.url},
            )
            try:
                with urllib.request.urlopen(request, timeout=2) as response:
                    response.read(4096)
            except (OSError, urllib.error.URLError):
                continue

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    after = 0
    session = None
    try:
        while rclpy.ok():
            try:
                query = {"after": str(after)}
                if session:
                    query["session"] = session
                with urllib.request.urlopen(
                    options.url + "/api/events?" + urllib.parse.urlencode(query),
                    timeout=4,
                ) as response:
                    event_type = "event"
                    while rclpy.ok():
                        line = read_sse_line(response)
                        if not line:
                            break
                        if line.startswith(b"event:"):
                            event_type = line[6:].strip().decode()
                        elif line.startswith(b"data:"):
                            events, cursor, observed_session = observation_data(
                                line[5:], event_type, after, session
                            )
                            for event in events:
                                payload = event_payload(event)
                                publisher.publish(String(data=payload))
                            after, session = cursor, observed_session
                            last_event[0] = time.monotonic()
                            rclpy.spin_once(node, timeout_sec=0)
            except (OSError, ValueError, urllib.error.URLError):
                time.sleep(1)
    finally:
        stop.set()
        thread.join(timeout=3)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
