import json
import urllib.request
from io import BytesIO

import pytest


def test_ros_event_preserves_original_sequence_and_clock():
    from alice.conversation.ros_observer import event_payload

    event = {
        "schema_version": "bench-event/v1",
        "session_id": "session-a",
        "sequence": 9,
        "monotonic_ns": 123456,
        "stage": "asr",
        "state": "final",
        "details": {"text": "Hello Alice"},
    }
    assert json.loads(event_payload(event)) == event


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"schema_version": "unknown"},
        {"schema_version": "bench-event/v1", "sequence": -1},
        {
            "schema_version": "bench-event/v1",
            "sequence": 1,
            "details": {"text": "x" * 100000},
        },
    ],
)
def test_ros_bridge_rejects_invalid_or_unbounded_payload(event):
    from alice.conversation.ros_observer import event_payload

    with pytest.raises(ValueError):
        event_payload(event)


def test_expired_cursor_full_reset_is_bounded_and_preserves_events():
    from alice.conversation.events import EventStore
    from alice.conversation.ros_observer import observation_data, read_sse_line

    store = EventStore()
    for _ in range(300):
        store.emit(
            "capture",
            "listening",
            source="alsa_input.usb-Seeed_Studio_ReSpeaker_Lite_0000000001-00.analog-stereo",
            rms=0.01,
            peak=0.2,
        )
    snapshot = store.snapshot()
    line = b"data:" + json.dumps(snapshot).encode() + b"\n"
    assert len(line) > 65536
    raw = read_sse_line(BytesIO(line))
    events, after, session = observation_data(raw[5:], "reset", 1, None)
    assert events == snapshot["events"]
    assert after == 300
    assert session == snapshot["session_id"]


def test_reset_recovers_after_host_restart_even_with_no_new_events():
    from alice.conversation.events import EventStore
    from alice.conversation.ros_observer import observation_data

    snapshot = EventStore().snapshot()
    events, after, session = observation_data(
        json.dumps(snapshot).encode(), "reset", 4000, "old-host-session"
    )
    assert events == []
    assert after == 0
    assert session == snapshot["session_id"]


def test_observer_accepts_valid_non_ascii_details_without_ascii_inflation():
    from alice.conversation.events import EventStore
    from alice.conversation.ros_observer import event_payload

    event = EventStore().emit("asr", "final", text="é" * 8000)
    assert json.loads(event_payload(event)) == event


def test_observer_rejects_unbounded_line_and_mismatched_reset_session():
    from alice.conversation.events import EventStore
    from alice.conversation.ros_observer import observation_data, read_sse_line

    with pytest.raises(ValueError, match="limit"):
        read_sse_line(BytesIO(b"x" * (16 * 1024 * 1024 + 1)))
    store = EventStore()
    store.emit("capture", "listening")
    snapshot = store.snapshot()
    snapshot["session_id"] = "different"
    with pytest.raises(ValueError, match="session"):
        observation_data(json.dumps(snapshot).encode(), "reset", 0, None)


@pytest.mark.parametrize(("count", "cursor"), [(300, 1), (0, 4000), (4, 2)])
def test_observer_resumes_real_sse_with_expired_or_prior_host_cursor(count, cursor):
    from alice.conversation.events import EventStore
    from alice.conversation.ros_observer import observation_data, read_sse_line
    from alice.conversation.web import serve

    store = EventStore()
    for _ in range(count):
        store.emit("capture", "active", source="ReSpeaker" * 10, rms=0.01, peak=0.1)
    server = serve(store, lambda _: {"ok": True}, port=0)
    try:
        url = (
            f"http://127.0.0.1:{server.server_port}/api/events?after={cursor}"
            "&session=prior-session"
        )
        with urllib.request.urlopen(url, timeout=3) as response:
            kind = "event"
            while line := read_sse_line(response):
                if line.startswith(b"event:"):
                    kind = line[6:].strip().decode()
                elif line.startswith(b"data:"):
                    events, after, session = observation_data(
                        line[5:], kind, cursor, "prior-session"
                    )
                    assert kind == "reset"
                    assert after == count
                    assert session == store.snapshot()["session_id"]
                    assert len(events) == min(count, 256)
                    break
            else:
                pytest.fail("server ended without cursor reset")
    finally:
        server.shutdown()
        server.server_close()
