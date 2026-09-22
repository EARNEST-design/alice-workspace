# Qwen ASR conversation bench implementation plan

**Goal:** Run attended ReSpeaker speech recognition through hosted Qwen ASR and
show the conversation chain in a local HTML dashboard with ROS observation.

**Architecture:** A host asyncio runtime owns bounded capture, local VAD,
hosted ASR, text-model requests and cancellable audio output. A separate local
HTTP/SSE UI and read-only ROS observer consume versioned runtime events.

**Tech stack:** Python 3.13, numpy, httpx, ONNX Runtime, native PipeWire tools,
existing PocketTtsWorker, stdlib HTTP server, HTML/JS, containerized ROS 2.

**Spec:** `docs/superpowers/specs/2026-09-21-live-conversation-bench-design.md`.

## Global constraints

- Preserve this linked worktree and all earlier local notes/artifacts.
- Default idle/dry operation. Explicit UI or CLI start enables bounded audio.
- Five-minute live session bound, 15-second utterance bound, 16 kHz mono ASR.
- Local Silero 6.2.2 ONNX: threshold 0.5, release 0.35, 300 ms silence,
  200 ms pre-roll, 512-sample frames. No ASR on VAD-negative silence.
- HTTPS ASR endpoint `https://work.manakin-gecko.ts.net:10000/v1`, model
  `qwen3-asr`, English, temperature 0, seed 7; verify TLS.
- Microphone audio goes only to the explicitly selected ASR server. Text goes
  to the configured MiniCPM/LM Studio endpoints. No persistent raw audio or
  operator transcript logging; no remote-admin endpoints or motor commands.
- Keep capture, inference, decision, output and observation interfaces separate.
- Reject late results after generation invalidation; Stop silences output
  before expensive cancellation/cleanup. Bound queues and surface overflow.
- Serve on `127.0.0.1:8765`; validate same-origin controls. ROS domain 74 with
  local-only discovery; absence of a bridge is shown as disconnected.
- Output digital peak at most 11572/32768. No claim of acoustic stop timing.
- Add failing behavioral tests first; run targeted tests, lint/type checks,
  actual synthetic endpoint smoke, browser test and ROS subscriber check.

## Task 1: Streaming ASR transport

Files: create `src/alice/conversation/asr.py`,
`tests/conversation/test_asr.py`. Package init supplied by coordinator.

Public interface:
```python
@dataclass(frozen=True)
class Transcript:
    text: str
    final: bool

class QwenAsrClient:
    def __init__(self, base_url: str, model: str = "qwen3-asr", *,
                 client: httpx.AsyncClient | None = None): ...
    async def transcribe(self, pcm: NDArray[np.float32]) -> AsyncIterator[Transcript]: ...
    async def close(self) -> None: ...
```

- [x] Write tests using real in-memory WAV parsing plus a controlled HTTP
  transport: multipart mono PCM16 at 16 kHz, language en/seed 7/model fields;
  split Qwen prefix, ordinary transcript chunks, exactly one final result;
  empty/nonfinite/overlong PCM rejected without network; length finish, SSE
  error, malformed chunks and EOF without DONE rejected; cancellation closes
  the response and propagates. No actual remote calls in unit tests.
- [x] Run `.venv/bin/python -m pytest tests/conversation/test_asr.py -q` and
  capture the expected missing-feature failure.
- [x] Implement a bounded multipart request with persistent client, ten-second
  total deadline, no redirects/retries, at most 2000 transcript characters and
  bounded SSE records. Parse metadata before emitting text. Require stop and
  DONE before emitting final. Close only owned clients.
- [x] Repeat the targeted tests and Ruff, then report evidence and changed files.

## Task 2: Event store and local dashboard

Files: create `src/alice/conversation/events.py`, `web.py`,
`src/alice/resources/conversation-dashboard.html`,
`tests/conversation/test_web.py`.

Public interface:
```python
class EventStore:
    def emit(self, stage: str, state: str, **details: object) -> dict: ...
    def snapshot(self) -> dict: ...
    def since(self, after: int) -> list[dict]: ...
    def wait(self, after: int, timeout: float = 1) -> list[dict]: ...

def serve(store: EventStore, control: Callable[[dict], dict], *,
          host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer: ...
```

`serve` starts a daemon HTTP serving thread and returns the owned server for
shutdown. Snapshot has `session_id`, `sequence`, `stages` (latest event per
stage), `events` (bounded history). Each event has `schema_version`, `session_id`,
`sequence`, `monotonic_ns`, `stage`, `state`, `details`. Stage data is JSON only.
Maximum history 256; arbitrary stage names cannot create unbounded state.

- [x] Write network tests against a real ephemeral loopback server: snapshot,
  SSE replay, history bounds, stale cursor reset, oversized body, foreign
  Origin/Host denied, unknown command denied, structured state updates and
  server shutdown. Browser text must be inserted with textContent.
- [x] Run tests to demonstrate missing implementation.
- [x] Implement `/`, `/api/state`, `/api/events?after=N`, `/api/control`.
  Controls: `start` with mode `listen` or `conversation`, `stop`, `replay`,
  `bridge` heartbeat. Runtime callback handles effects; HTTP does not access
  hardware. Reject malformed requests before invoking it. Loopback only.
- [x] Build a clear dashboard with capture/VAD/ASR/decision/LLM/TTS/output/ROS
  stages, level and probability meters, endpoint countdown, partial/final
  transcript, streamed reply, per-stage timing, connection age and event list.
  Start/Stop/listen/conversation/replay controls; no fabricated stage activity.
- [x] Repeat tests and report evidence. Coordinator performs live browser QA.

## Task 3: Runtime, audio, model clients and ROS observation

Files: create `src/alice/conversation/vad.py`, `audio.py`, `models.py`,
`runtime.py`, `cli.py`, `ros_observer.py`, `__init__.py`; tests under
`tests/conversation/`; add `conversation` extra/CLI to pyproject and lockfile.

- [x] Test VAD with literal probability/frame sequences: no silence dispatch,
  pre-roll and endpoint, short noise rejection, hard utterance limit.
- [x] Test runtime with controlled ASR/decision/reply/output adapters: one final
  commit, stale/duplicate results rejected, Stop priority, playback guard,
  error cleanup, bounds and stop/restart isolation. Use `asyncio.run`.
- [x] Implement local ONNX VAD identity/hash verification, captured audio via
  `pw-cat` owned subprocess, verified ReSpeaker source/sink and PCM conversion.
  Fault on a stalled/disconnected input; terminate only owned processes.
- [x] Implement MiniCPM advisory requests outside capture and streamed Qwen
  text-only completions with explicit non-thinking prefix. Keep the deterministic
  turn admission independent of advice; retain bounded text context.
- [x] Reuse PocketTtsWorker for short complete clauses, capped PCM, native
  PipeWire output, immediate process cancellation and measured drain. Mark
  playback timing as software observations. First mode has an explicit echo
  guard; barge-in is experimental and opt-in, not advertised as qualified.
- [x] Wire EventStore, HTTP commands and CLI. A configured synthetic replay
  fixture permits an end-to-end demo with live ASR/LLMs and dry output. The
  first bench defers audible dashboard replay; --enable-audio controls live
  Conversation output. Warm TTS/VAD before capture.
- [x] Add device-free ROS observer consuming HTTP events and publishing
  `/alice/conversation/events` as std_msgs/String; local heartbeat posted to
  runtime with stale expiry. Verify with another ROS subscriber.
- [x] Run targeted tests, speech regressions, Ruff/mypy and browser checks.
  Exercise synthetic replay over actual endpoints, then leave the dashboard
  ready for operator-driven live listening. Record actual metrics, errors and
  limitations; update checkpoint and runbook. Obtain independent code review
  and resolve important findings before reporting completion.

## Completion evidence

Implemented and independently reviewed on 2026-09-21. Final 92 conversation
tests and 160 existing speech tests pass; Ruff, strict mypy and lock checks pass.
See `docs/experiments/2026-09-21-qwen-asr-bench.md` for actual synthetic/physical
output and two bounded live-window results. A successfully transcribed live
human phrase, full duplex and continuous speaking policy remain unqualified.
Explicit initial-bench deferrals are recorded in the runbook and ADR 0015.
