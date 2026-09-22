"""Network and browser-contract tests for the local conversation dashboard."""

from __future__ import annotations

import http.client
import json
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from html.parser import HTMLParser
from importlib.resources import files
from typing import Any

import pytest

from alice.conversation.events import EventStore
from alice.conversation.web import serve


@contextmanager
def running_server(
    calls: list[dict[str, object]],
) -> Iterator[tuple[http.client.HTTPConnection, int]]:
    def control(command: dict[str, object]) -> dict[str, object]:
        calls.append(command)
        return {"ok": True, "accepted": command["action"]}

    server = serve(EventStore(), control, port=0)
    port = server.server_address[1]
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        yield connection, port
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def read_json(response: http.client.HTTPResponse) -> dict[str, Any]:
    return json.loads(response.read())


def post_json(
    connection: http.client.HTTPConnection,
    path: str,
    payload: dict[str, object],
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    connection.request("POST", path, json.dumps(payload), request_headers)
    response = connection.getresponse()
    return response.status, read_json(response)


def test_event_store_has_versioned_bounded_json_history_and_stage_state() -> None:
    store = EventStore()
    first = store.emit("capture", "ready", rms=0.125, generation_id=3)

    assert first["schema_version"] == "bench-event/v1"
    assert first["sequence"] == 1
    assert first["stage"] == "capture"
    assert first["details"] == {"rms": 0.125, "generation_id": 3}
    assert isinstance(first["session_id"], str)
    assert isinstance(first["monotonic_ns"], int)
    assert store.since(0) == [first]

    for index in range(300):
        store.emit(f"extension-{index}", "observed", message=str(index))

    snapshot = store.snapshot()
    assert snapshot["sequence"] == 301
    assert len(snapshot["events"]) == 256
    assert len(snapshot["stages"]) <= 32
    assert snapshot["events"][0]["sequence"] == 46
    json.dumps(snapshot, allow_nan=False)

    with pytest.raises((TypeError, ValueError)):
        store.emit("capture", "bad", message=object())
    with pytest.raises((TypeError, ValueError)):
        store.emit("capture", "bad", rms=float("nan"))


def test_wait_unblocks_only_for_a_newer_event() -> None:
    store = EventStore()
    observed: list[list[dict[str, Any]]] = []
    waiter = threading.Thread(target=lambda: observed.append(store.wait(0, timeout=1)))
    waiter.start()
    time.sleep(0.02)
    emitted = store.emit("session", "idle", message="ready")
    waiter.join(timeout=1)

    assert observed == [[emitted]]
    assert store.wait(emitted["sequence"], timeout=0.01) == []


def test_root_and_state_are_served_from_a_real_loopback_server() -> None:
    calls: list[dict[str, object]] = []
    with running_server(calls) as (connection, port):
        connection.request("GET", "/")
        page = connection.getresponse()
        html = page.read().decode()
        assert page.status == 200
        assert page.getheader("Content-Type") == "text/html; charset=utf-8"
        assert "Conversation bench" in html

        class ControlLabels(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.current: str | None = None
                self.labels: dict[str, str] = {}
                self.all_text: list[str] = []

            def handle_starttag(
                self,
                tag: str,
                attrs: list[tuple[str, str | None]],
            ) -> None:
                attributes = dict(attrs)
                if tag == "option":
                    self.current = f"option:{attributes.get('value')}"
                elif tag == "button" and attributes.get("id") == "start":
                    self.current = "button:start"

            def handle_endtag(self, tag: str) -> None:
                if tag in {"option", "button"}:
                    self.current = None

            def handle_data(self, data: str) -> None:
                self.all_text.append(data)
                if self.current is not None:
                    self.labels[self.current] = self.labels.get(self.current, "") + data

        labels = ControlLabels()
        labels.feed(html)
        assert labels.labels["option:listen"].strip() == "Wake listening"
        assert (
            labels.labels["option:conversation"].strip()
            == "Conversation (wake required)"
        )
        assert labels.labels["button:start"].strip() == "Start wake listening"
        assert "MiniCPM advice" not in " ".join(labels.all_text)

        connection.request("GET", "/api/state")
        response = connection.getresponse()
        state = read_json(response)
        assert response.status == 200
        assert state["sequence"] == 0
        assert state["stages"] == {}
        assert state["events"] == []


def test_sse_replays_events_and_resets_an_expired_cursor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = EventStore()
    server = serve(store, lambda command: {"ok": True}, port=0)
    port = server.server_address[1]
    try:
        store.emit("capture", "ready", rms=0.1)
        final = store.emit("asr", "final", text="hello")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request("GET", "/api/events?after=1")
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Type") == "text/event-stream"
        assert response.readline() == b"id: 2\n"
        assert response.readline() == b"event: event\n"
        data = json.loads(response.readline().removeprefix(b"data: "))
        assert data == final
        connection.close()

        for index in range(256):
            store.emit("capture", "level", rms=index / 256)
        stale = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        stale.request("GET", "/api/events?after=0")
        reset = stale.getresponse()
        assert reset.readline() == b"event: reset\n"
        reset_data = json.loads(reset.readline().removeprefix(b"data: "))
        assert reset_data["sequence"] == 258
        assert len(reset_data["events"]) == 256
        stale.close()
        time.sleep(0.02)
    finally:
        server.shutdown()
        server.server_close()
    assert "Exception occurred during processing" not in capsys.readouterr().err


def test_sse_resets_when_client_session_differs_despite_valid_sequence() -> None:
    store = EventStore()
    for index in range(6):
        store.emit("capture", "active", rms=index / 10)
    current_session = store.snapshot()["session_id"]
    server = serve(store, lambda command: {"ok": True}, port=0)
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=2
    )
    try:
        connection.request(
            "GET",
            "/api/events?after=5&session=retired-session",
        )
        response = connection.getresponse()
        assert response.status == 200
        assert response.readline() == b"event: reset\n"
        snapshot = json.loads(response.readline().removeprefix(b"data: "))
        assert snapshot["session_id"] == current_session
        assert snapshot["sequence"] == 6
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def test_sse_subscribers_are_bounded() -> None:
    server = serve(EventStore(), lambda command: {"ok": True}, port=0)
    port = server.server_address[1]
    connections: list[http.client.HTTPConnection] = []
    statuses: list[int] = []
    try:
        for _ in range(12):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request("GET", "/api/events?after=0")
            response = connection.getresponse()
            statuses.append(response.status)
            if response.status != 200:
                response.read()
            connections.append(connection)
        assert 200 in statuses
        assert 503 in statuses
    finally:
        for connection in connections:
            connection.close()
        server.shutdown()
        server.server_close()


def test_connected_sse_subscriber_gets_reset_after_history_gap(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class HeldWaitStore(EventStore):
        def __init__(self) -> None:
            super().__init__()
            self.waiting = threading.Event()
            self.release = threading.Event()

        def wait(self, after: int, timeout: float = 1) -> list[dict[str, Any]]:
            self.waiting.set()
            assert self.release.wait(timeout=2)
            return super().wait(after, timeout=0)

    store = HeldWaitStore()
    store.emit("session", "idle", message="ready")
    server = serve(store, lambda command: {"ok": True}, port=0)
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=2
    )
    try:
        connection.request("GET", "/api/events?after=1")
        response = connection.getresponse()
        assert response.status == 200
        assert store.waiting.wait(timeout=1)
        for index in range(300):
            store.emit("capture", "active", rms=index / 300)
        store.release.set()

        assert response.readline() == b"event: reset\n"
        snapshot = json.loads(response.readline().removeprefix(b"data: "))
        assert snapshot["sequence"] == 301
        assert snapshot["events"][0]["sequence"] == 46
    finally:
        store.release.set()
        connection.close()
        time.sleep(0.02)
        server.shutdown()
        server.server_close()
    assert "Exception occurred during processing" not in capsys.readouterr().err


def test_idle_sse_stream_sends_application_heartbeat() -> None:
    server = serve(EventStore(), lambda command: {"ok": True}, port=0)
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=2
    )
    try:
        connection.request("GET", "/api/events?after=0")
        response = connection.getresponse()
        assert response.status == 200
        assert response.readline() == b"event: heartbeat\n"
        heartbeat = json.loads(response.readline().removeprefix(b"data: "))
        assert heartbeat["schema_version"] == "bench-event/v1"
        assert heartbeat["sequence"] == 0
        assert heartbeat["state"] == "heartbeat"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("mode", ["listen", "conversation"])
def test_start_control_validates_mode_then_invokes_callback(mode: str) -> None:
    calls: list[dict[str, object]] = []
    with running_server(calls) as (connection, port):
        status, result = post_json(
            connection,
            "/api/control",
            {"action": "start", "mode": mode},
            headers={"Origin": f"http://127.0.0.1:{port}"},
        )

    assert status == 200
    assert result == {"ok": True, "accepted": "start"}
    assert calls == [{"action": "start", "mode": mode}]


@pytest.mark.parametrize("action", ["stop", "replay", "bridge"])
def test_bounded_control_actions_invoke_callback(action: str) -> None:
    calls: list[dict[str, object]] = []
    with running_server(calls) as (connection, _port):
        status, result = post_json(connection, "/api/control", {"action": action})

    assert status == 200
    assert result["ok"] is True
    assert calls == [{"action": action}]


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "launch"},
        {"action": "start"},
        {"action": "start", "mode": "barge-in"},
        {"action": "stop", "mode": "listen"},
        {"action": "stop", "extra": True},
    ],
)
def test_invalid_controls_are_rejected_before_callback(
    payload: dict[str, object],
) -> None:
    calls: list[dict[str, object]] = []
    with running_server(calls) as (connection, _port):
        status, result = post_json(connection, "/api/control", payload)

    assert status == 400
    assert result["ok"] is False
    assert calls == []


def test_oversized_and_cross_origin_controls_are_rejected_before_callback() -> None:
    calls: list[dict[str, object]] = []
    with running_server(calls) as (connection, port):
        connection.request(
            "POST",
            "/api/control",
            b"x" * 4097,
            {"Content-Type": "application/json"},
        )
        oversized = connection.getresponse()
        assert oversized.status == 413
        oversized.read()

        origin_connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        status, _ = post_json(
            origin_connection,
            "/api/control",
            {"action": "stop"},
            headers={"Origin": "https://operator.example"},
        )
        origin_connection.close()
        assert status == 403

        cross_loopback = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        status, _ = post_json(
            cross_loopback,
            "/api/control",
            {"action": "stop"},
            headers={"Origin": f"http://localhost:{port}"},
        )
        cross_loopback.close()
        assert status == 403

        foreign = http.client.HTTPConnection("127.0.0.1", connection.port, timeout=2)
        status, _ = post_json(
            foreign,
            "/api/control",
            {"action": "stop"},
            headers={"Host": "operator.example"},
        )
        foreign.close()
        assert status == 403

    assert calls == []


def test_serve_rejects_non_loopback_binding_and_shutdown_closes_listener() -> None:
    with pytest.raises(ValueError, match="127.0.0.1"):
        serve(EventStore(), lambda command: {}, host="0.0.0.0", port=0)

    server = serve(EventStore(), lambda command: {}, port=0)
    port = server.server_address[1]
    server.shutdown()
    server.server_close()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.1)


def test_dashboard_renders_untrusted_model_text_as_text() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; required for dashboard JS regression")
    template = (
        files("alice.resources").joinpath("conversation-dashboard.html").read_text()
    )
    script = template.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    harness = r"""
class Element {
  constructor(id='') { this.id=id; this.textContent=''; this.style={}; this.dataset={};
    this.disabled=false; this.value='listen'; this.children=[]; }
  addEventListener() {}
  append(child) { this.children.push(child); }
  prepend(child) { this.children.unshift(child); }
  remove() {}
}
const elements = new Map();
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id,new Element(id));
    return elements.get(id);
  },
  querySelectorAll(selector) {
    const stage = selector.match(/data-stage="([^"]+)"/);
    return stage ? [this.getElementById(`card-${stage[1]}`)] : [];
  },
  createElement() { return new Element(); }
};
const window = { addEventListener() {} };
const globalThis = window;
class EventSource { addEventListener() {} close() {} }
function fetch() { return Promise.resolve({ok:true,json:()=>Promise.resolve({})}); }
function setInterval() {}
"""
    assertion = r"""
window.__aliceDashboard.applyEvent({stage:'asr',state:'partial',sequence:1,
  monotonic_ns:1,details:{text:'<img src=x onerror=alert(1)>'}});
const rendered = document.getElementById('transcript').textContent;
if (rendered !== '<img src=x onerror=alert(1)>') process.exit(2);
window.__aliceDashboard.applyEvent({stage:'llm',state:'requesting',sequence:2,
  monotonic_ns:2,details:{generation_id:'g1'}});
window.__aliceDashboard.applyEvent({stage:'llm',state:'streaming',sequence:3,
  monotonic_ns:3,details:{generation_id:'g1',text:'Hello'}});
window.__aliceDashboard.applyEvent({stage:'llm',state:'streaming',sequence:4,
  monotonic_ns:4,details:{generation_id:'g1',text:'Hello world'}});
if (document.getElementById('reply').textContent !== 'Hello world') process.exit(3);
window.__aliceDashboard.applyEvent({stage:'llm',state:'complete',sequence:5,
  monotonic_ns:5,details:{generation_id:'g1',text:'Hello world'}});
if (document.getElementById('reply').className !== 'speech-text') process.exit(4);
window.__aliceDashboard.applyEvent({stage:'output',state:'dry',sequence:6,
  monotonic_ns:6,details:{message:'Dry speaker'}});
if (document.getElementById('card-output').dataset.active !== 'false') process.exit(5);
window.__aliceDashboard.applyEvent({stage:'llm',state:'streaming',sequence:7,
  monotonic_ns:7,details:{generation_id:'g1',text:'Next'}});
if (document.getElementById('card-llm').dataset.active !== 'true') process.exit(6);
window.__aliceDashboard.applyEvent({stage:'session',state:'listening',sequence:8,
  monotonic_ns:8,details:{mode:'conversation'}});
if (document.getElementById('mode').value !== 'conversation') process.exit(7);
if (document.getElementById('start').textContent !==
    'Start conversation · wake required') {
  process.exit(8);
}
window.__aliceDashboard.applyEvent({stage:'asr',state:'ignored',sequence:9,
  monotonic_ns:9,details:{text:'',language:'Arabic',
    message:'Skipped Arabic result; English mode'}});
if (document.getElementById('transcript').textContent !==
    'Skipped Arabic result; English mode') process.exit(9);
if (document.getElementById('transcript').className !== 'speech-text') process.exit(10);
if (document.getElementById('detected-language').textContent !== 'Arabic') {
  process.exit(11);
}
if (document.getElementById('transcript-note').textContent !==
    'Skipped Arabic result; English mode') process.exit(12);
window.__aliceDashboard.applyEvent({stage:'asr',state:'ready',sequence:10,
  monotonic_ns:10,details:{languages:['English']}});
const englishOnly = document.getElementById('asr-languages').textContent;
if (englishOnly !== 'English' || englishOnly.includes('Cantonese')) process.exit(13);
window.__aliceDashboard.applyEvent({stage:'asr',state:'ready',sequence:11,
  monotonic_ns:11,details:{languages:['English','Cantonese']}});
if (document.getElementById('asr-languages').textContent !==
    'English · Cantonese') process.exit(14);
window.__aliceDashboard.applyEvent({stage:'asr',state:'ignored',sequence:12,
  monotonic_ns:12,details:{text:'背景說話',language:'Cantonese',
    message:'Ignored during a bounded listen-only probe'}});
if (document.getElementById('transcript').textContent !== '背景說話') process.exit(15);
if (document.getElementById('transcript-note').textContent !==
    'Ignored during a bounded listen-only probe') process.exit(16);
if (document.getElementById('detected-language').textContent !== 'Cantonese') {
  process.exit(17);
}
window.__aliceDashboard.applyEvent({stage:'tts',state:'generating',sequence:13,
  monotonic_ns:13,details:{language:'Cantonese',voice:'Canto TTS Nano'}});
if (document.getElementById('tts-voice').textContent !==
    'Cantonese · Canto TTS Nano') process.exit(18);
window.__aliceDashboard.applyEvent({stage:'asr',state:'unavailable',sequence:14,
  monotonic_ns:14,details:{error:'backend offline'}});
if (document.getElementById('asr-languages').textContent !==
    'Backend unavailable') process.exit(19);
window.__aliceDashboard.applyEvent({stage:'tts',state:'error',sequence:15,
  monotonic_ns:15,details:{error:'voice unavailable'}});
if (document.getElementById('tts-voice').textContent !== 'Unavailable') {
  process.exit(20);
}
window.__aliceDashboard.applyEvent({stage:'capture',state:'active',sequence:16,
  monotonic_ns:16,details:{rms:0.1,peak:0.2}});
window.__aliceDashboard.applyEvent({stage:'engagement',state:'waiting',sequence:17,
  monotonic_ns:17,details:{message:'Say Alice, 愛麗絲, or 爱丽丝 to begin.'}});
if (document.getElementById('stage-engagement-state').textContent !==
    'Waiting for Alice / 愛麗絲') process.exit(21);
if (document.getElementById('card-engagement').dataset.active !== 'false') {
  process.exit(22);
}
window.__aliceDashboard.applyEvent({stage:'engagement',state:'awake',sequence:18,
  monotonic_ns:18,details:{remaining_seconds:30,
    message:'Wake name heard; follow-ups enabled for 30 seconds.'}});
if (document.getElementById('stage-engagement-state').textContent !==
    'Wake window active') process.exit(23);
if (document.getElementById('stage-engagement-latency').textContent !== '30.0 s') {
  process.exit(24);
}
if (document.getElementById('card-engagement').dataset.active !== 'true') {
  process.exit(25);
}
window.__aliceDashboard.applyEvent({stage:'capture',state:'stopped',sequence:19,
  monotonic_ns:19,details:{}});
if (document.getElementById('stage-engagement-state').textContent !== 'inactive') {
  process.exit(26);
}
if (!document.getElementById('stage-engagement-detail').textContent
    .includes('microphone stopped')) process.exit(27);
if (document.getElementById('card-engagement').dataset.active !== 'false') {
  process.exit(28);
}
window.__aliceDashboard.applyEvent({stage:'capture',state:'active',sequence:20,
  monotonic_ns:20,details:{rms:0.1,peak:0.2}});
window.__aliceDashboard.applyEvent({stage:'engagement',state:'expired',sequence:21,
  monotonic_ns:21,details:{remaining_seconds:0,
    message:'Follow-up window expired; awaiting wake name.'}});
if (document.getElementById('stage-engagement-state').textContent !==
    'Waiting for Alice / 愛麗絲') process.exit(29);
if (document.getElementById('card-engagement').dataset.active !== 'false') {
  process.exit(30);
}
window.__aliceDashboard.applyEvent({stage:'decision',state:'advice',sequence:22,
  monotonic_ns:22,details:{text:'WAIT',reason:'awaiting_wake',
    message:'Awaiting wake name; no model request made.'}});
if (document.getElementById('turn-decision').textContent !== 'WAIT') process.exit(31);
if (document.getElementById('turn-decision-note').textContent !==
    'Awaiting wake name; no model request made.') process.exit(32);
window.__aliceDashboard.applyEvent({stage:'quality',state:'ready',sequence:23,
  monotonic_ns:23,details:{evidence:{asr_confidence:null,language_confidence:null,
    overlap:{max_simultaneous_speakers:2,max_window_speakers:2,overlap_seconds:.4}}}});
if (document.getElementById('asr-confidence').textContent !==
    'Unknown / unknown') process.exit(33);
if (!document.getElementById('speaker-evidence').textContent
    .includes('2 simultaneous')) process.exit(34);
window.__aliceDashboard.applyEvent({stage:'decision',state:'advice',sequence:24,
  monotonic_ns:24,details:{text:'WAIT',speak_probability:.6,coherence_probability:.9}});
if (document.getElementById('decision-probability').textContent !==
    'Speak 60% · coherent 90%') process.exit(35);
window.__aliceDashboard.applyEvent({stage:'asr',state:'uploading',sequence:25,
  monotonic_ns:25,details:{}});
if (document.getElementById('decision-probability').textContent !==
    'Awaiting decision') process.exit(36);
if (document.getElementById('speaker-evidence').textContent !==
    'Awaiting audio analysis') process.exit(37);
window.__aliceDashboard.applyEvent({stage:'quality',state:'ready',sequence:26,
  monotonic_ns:26,details:{evidence:{overlap:{}}}});
const missing = document.getElementById('speaker-evidence').textContent;
if (missing.includes('0.00') || missing.includes('undefined') ||
    !missing.includes('unknown')) process.exit(38);


"""
    result = subprocess.run(
        [node, "-e", harness + script + assertion],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr


def test_dashboard_reconnects_from_cursor_and_expires_live_freshness() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is optional; required for dashboard JS regression")
    template = (
        files("alice.resources").joinpath("conversation-dashboard.html").read_text()
    )
    script = template.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    harness = r"""
let now = 1000;
Date.now = () => now;
let intervalCallback = null;
let timeoutCallback = null;
class Element {
  constructor(id='') {
    this.id=id; this.textContent=''; this.style={}; this.dataset={};
    this.disabled=false; this.value='listen'; this.children=[]; this.parent=null;
  }
  addEventListener() {}
  append(...children) {
    for (const child of children) { child.parent=this; this.children.push(child); }
  }
  prepend(child) { child.parent=this; this.children.unshift(child); }
  replaceChildren(...children) { this.children=[]; this.append(...children); }
  remove() {
    if (!this.parent) return;
    this.parent.children=this.parent.children.filter(child => child !== this);
  }
}
const elements = new Map();
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id,new Element(id));
    return elements.get(id);
  },
  querySelectorAll(selector) {
    const stage = selector.match(/data-stage="([^"]+)"/);
    return stage ? [this.getElementById(`card-${stage[1]}`)] : [];
  },
  createElement() { return new Element(); }
};
const sources=[];
class EventSource {
  constructor(url) {
    this.url=url; this.listeners={}; this.closed=false; sources.push(this);
  }
  addEventListener(name, callback) { this.listeners[name]=callback; }
  close() { this.closed=true; }
}
const window = { addEventListener() {} };
const globalThis = window;
function fetch() { return new Promise(() => {}); }
function setInterval(callback) { intervalCallback=callback; }
function setTimeout(callback) { timeoutCallback=callback; return 1; }
function clearTimeout() { timeoutCallback=null; }
"""
    assertion = r"""
const initialEvent={schema_version:'bench-event/v1',
  session_id:'session-a',sequence:12,monotonic_ns:12,stage:'asr',state:'final',
  details:{text:'hello'}};
window.__aliceDashboard.renderSnapshot({session_id:'session-a',sequence:12,
  events:[initialEvent],stages:{asr:initialEvent}});
window.__aliceDashboard.openEvents(12);
if (sources[0].url !== '/api/events?after=12&session=session-a') process.exit(2);
sources[0].onerror();
if (!sources[0].closed || timeoutCallback === null) process.exit(3);
timeoutCallback();
if (sources[1].url !== '/api/events?after=12&session=session-a') process.exit(4);

sources[1].onopen();
now = 5000;
intervalCallback();
const staleState = document.getElementById('connection-state').textContent;
if (staleState !== 'stale') process.exit(5);
sources[1].listeners.heartbeat({data:JSON.stringify({
  schema_version:'bench-event/v1',session_id:'session-a',sequence:12,
  monotonic_ns:13,stage:'session',state:'heartbeat',details:{}})});
if (document.getElementById('connection-state').textContent !== 'live') process.exit(6);

const oldRos={schema_version:'bench-event/v1',session_id:'session-a',sequence:11,
  monotonic_ns:11,stage:'ros',state:'heartbeat',details:{message:'old'}};
const snapshot={session_id:'session-a',sequence:12,events:[oldRos],stages:{ros:oldRos}};
window.__aliceDashboard.renderSnapshot(snapshot);
window.__aliceDashboard.renderSnapshot(snapshot);
if (document.getElementById('stage-ros-state').textContent !== 'stale') process.exit(7);
if (document.getElementById('card-ros').dataset.active !== 'false') process.exit(8);
if (document.getElementById('timeline').children.length !== 1) process.exit(9);
now = 9000;
intervalCallback();
sources[1].listeners.heartbeat({data:JSON.stringify({
  schema_version:'bench-event/v1',session_id:'old-session',sequence:99,
  monotonic_ns:99,stage:'session',state:'heartbeat',details:{}})});
const oldSessionState = document.getElementById('connection-state').textContent;
if (oldSessionState !== 'stale') process.exit(10);
sources[1].listeners.reset({data:JSON.stringify({
  session_id:'session-b',sequence:20,events:[],stages:{}})});
sources[1].onerror();
timeoutCallback();
if (sources[2].url !== '/api/events?after=20&session=session-b') process.exit(11);
"""
    result = subprocess.run(
        [node, "-e", harness + script + assertion],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
