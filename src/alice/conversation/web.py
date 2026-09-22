"""Loopback-only HTTP and SSE server for the conversation bench dashboard."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import cast
from urllib.parse import parse_qs, urlsplit

from alice.conversation.events import Event, EventStore, JsonValue

type Control = Callable[[dict[str, object]], dict[str, object]]

_BODY_LIMIT = 4096
_RESULT_LIMIT = 16_384
_SSE_CLIENT_LIMIT = 8


class _DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], store: EventStore, control: Control):
        super().__init__(address, _DashboardHandler)
        self.store = store
        self.control = control
        self.stop_event = threading.Event()
        self.sse_slots = threading.BoundedSemaphore(_SSE_CLIENT_LIMIT)
        bound_host, bound_port = cast(tuple[str, int], self.server_address)
        self.allowed_hosts = {
            f"{bound_host}:{bound_port}",
            f"localhost:{bound_port}",
        }

    def shutdown(self) -> None:
        self.stop_event.set()
        super().shutdown()


class _DashboardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: _DashboardServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._valid_host():
            self._json_error(HTTPStatus.FORBIDDEN, "foreign Host is not allowed")
            return
        parsed = urlsplit(self.path)
        if parsed.path == "/":
            page = (
                files("alice.resources")
                .joinpath("conversation-dashboard.html")
                .read_bytes()
            )
            self._send_bytes(
                HTTPStatus.OK,
                page,
                "text/html; charset=utf-8",
                csp=True,
            )
        elif parsed.path == "/api/state" and not parsed.query:
            self._send_json(HTTPStatus.OK, self.server.store.snapshot())
        elif parsed.path == "/api/events":
            self._events(parsed.query)
        else:
            self._json_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if urlsplit(self.path).path != "/api/control" or urlsplit(self.path).query:
            self.close_connection = True
            self._json_error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not self._valid_host() or not self._valid_origin():
            self.close_connection = True
            self._json_error(HTTPStatus.FORBIDDEN, "cross-origin control denied")
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self.close_connection = True
            self._json_error(HTTPStatus.BAD_REQUEST, "chunked bodies are not accepted")
            return
        content_length = self.headers.get("Content-Length")
        if content_length is None or not content_length.isdecimal():
            self.close_connection = True
            self._json_error(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
            return
        length = int(content_length)
        if length > _BODY_LIMIT:
            self.close_connection = True
            self._json_error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "request body too large",
            )
            return
        if self.headers.get_content_type() != "application/json":
            self.close_connection = True
            self._json_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "JSON body required")
            return
        try:
            decoded = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json_error(HTTPStatus.BAD_REQUEST, "malformed JSON")
            return
        command = self._validate_command(decoded)
        if command is None:
            self._json_error(HTTPStatus.BAD_REQUEST, "invalid control command")
            return
        try:
            result = self.server.control(command)
            body = json.dumps(
                result,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if not isinstance(result, dict) or len(body) > _RESULT_LIMIT:
                raise ValueError("invalid control result")
        except Exception:
            self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, "control failed")
            return
        self._send_bytes(HTTPStatus.OK, body, "application/json; charset=utf-8")

    @staticmethod
    def _validate_command(value: object) -> dict[str, object] | None:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            return None
        command = cast(dict[str, object], value)
        action = command.get("action")
        if action == "start":
            if set(command) != {"action", "mode"}:
                return None
            if command.get("mode") not in {"listen", "conversation"}:
                return None
            return command
        if action in {"stop", "replay", "bridge"} and set(command) == {"action"}:
            return command
        return None

    def _events(self, query: str) -> None:
        parameters = parse_qs(query, keep_blank_values=True)
        if (
            "after" not in parameters
            or not set(parameters) <= {"after", "session"}
            or len(parameters["after"]) != 1
        ):
            self._json_error(HTTPStatus.BAD_REQUEST, "one event cursor is required")
            return
        session_values = parameters.get("session")
        if session_values is not None and (
            len(session_values) != 1
            or not session_values[0]
            or len(session_values[0]) > 128
        ):
            self._json_error(HTTPStatus.BAD_REQUEST, "invalid event session")
            return
        session_id = session_values[0] if session_values is not None else None
        try:
            after = int(parameters["after"][0])
            if after < 0:
                raise ValueError
        except ValueError:
            self._json_error(HTTPStatus.BAD_REQUEST, "invalid event cursor")
            return
        if not self.server.sse_slots.acquire(blocking=False):
            self._json_error(HTTPStatus.SERVICE_UNAVAILABLE, "too many event streams")
            return
        try:
            self.close_connection = True
            expired, replay = self.server.store._replay(
                after,
                session_id=session_id,
            )
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if expired:
                snapshot = self.server.store.snapshot()
                self._write_sse("reset", snapshot)
                after = _json_integer(snapshot["sequence"], "snapshot sequence")
            else:
                after = self._write_events(replay, after)
            while not self.server.stop_event.is_set():
                events = self.server.store.wait(after, timeout=1.0)
                if events:
                    expired, replay = self.server.store._replay(after)
                    if expired:
                        snapshot = self.server.store.snapshot()
                        self._write_sse("reset", snapshot)
                        after = _json_integer(
                            snapshot["sequence"],
                            "snapshot sequence",
                        )
                    else:
                        after = self._write_events(replay, after)
                else:
                    self._write_sse("heartbeat", self.server.store._heartbeat())
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        finally:
            self.server.sse_slots.release()

    def _write_events(self, events: list[Event], after: int) -> int:
        for event in events:
            sequence = _json_integer(event["sequence"], "event sequence")
            self._write_sse("event", event, event_id=sequence)
            after = sequence
        return after

    def _write_sse(
        self,
        event_name: str,
        data: dict[str, JsonValue],
        *,
        event_id: int | None = None,
    ) -> None:
        if event_id is not None:
            self.wfile.write(f"id: {event_id}\n".encode("ascii"))
        self.wfile.write(f"event: {event_name}\n".encode("ascii"))
        payload = json.dumps(data, allow_nan=False, separators=(",", ":"))
        self.wfile.write(f"data: {payload}\n\n".encode())
        self.wfile.flush()

    def _valid_host(self) -> bool:
        return self.headers.get("Host", "") in self.server.allowed_hosts

    def _valid_origin(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin == f"http://{self.headers.get('Host', '')}"

    def _json_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"ok": False, "error": message})

    def _send_json(self, status: HTTPStatus, value: dict[str, JsonValue]) -> None:
        body = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        csp: bool = False,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if self.close_connection:
            self.send_header("Connection", "close")
        if csp:
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'none'",
            )
        self.end_headers()
        self.wfile.write(body)


def _json_integer(value: JsonValue, description: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"invalid {description}")
    return value


def serve(
    store: EventStore,
    control: Control,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Start the loopback dashboard on a daemon thread and return its server."""

    if host != "127.0.0.1":
        raise ValueError("dashboard host must be 127.0.0.1")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
        raise ValueError("port must be an integer from 0 through 65535")
    server = _DashboardServer((host, port), store, control)
    thread = threading.Thread(
        target=server.serve_forever,
        name="conversation-dashboard",
        daemon=True,
    )
    thread.start()
    return server
