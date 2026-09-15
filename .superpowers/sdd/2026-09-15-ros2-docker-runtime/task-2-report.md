# Task 2 implementation report

## Status

DONE_WITH_CONCERNS. The typed ROS contracts, generated-class converters, and
transactional transport guards are implemented and verified without hardware.
The planned single `alice_nodes/contracts.py` file is now 889 lines because it
contains all generated-message conversions and the three requested status
validators. It remains focused on one responsibility and was not split because
the task explicitly fixed the file layout; the coordinator should decide
whether a later task separates status validation.

## Implementation

- Added the `alice_interfaces` `ament_cmake` package with 16 bounded messages,
  two services, and one action. All active-run traffic carries `RunIdentity`;
  every stream uses `StreamHeader` with the original monotonic source time,
  sequence, and publisher incarnation.
- Added the `alice_nodes` `ament_python` package. `transport.py` contains frozen
  identities/bindings, PREPARE/START lifecycle enforcement, terminal latching,
  exact/latest stream guards, cumulative PCM credits, and transactional PCM
  packet admission. `contracts.py` converts through the existing Pydantic
  `SpeechClause`, `SpeechFrame`, `TargetUpdate`, and `BlendshapeObservation`
  contracts and validates run controls and status evidence.
- PCM is mono float data bounded to 3,840 wire samples and at most 20 ms at the
  declared 8--192 kHz sample rate. Its first packet carries the complete
  committed clause; later packets carry clause identity and the global sample
  offset. Empty PCM is accepted only as an explicit clause-final marker.
- Credit is cumulative and generation scoped. `sent - cumulative_consumed`
  includes in-flight PCM; acknowledgements cannot exceed sent samples, repeated
  cumulative values add no credit, and changed identities are ignored.
- Status validation distinguishes accepted EndRun from completed finalization,
  requires complete playback drain evidence, and limits servo receipts to the
  selected channel/name map. A servo receipt records controller evidence only.

## Exact API for Task 3

Lifecycle endpoints are `/alice/run_speech` (`RunSpeech`) and per-node
`/alice/<node>/begin_run` (`BeginRun`) plus `/alice/<node>/end_run` (`EndRun`).
The eight roster names are exactly `session`, `tts`, `audio`, `expression`,
`motion`, `maestro`, `perception`, and `recorder`.

`BeginRun` request schema `begin-run/v1` carries identity, operation
`PREPARE|START`, profile, seed, hardware flag, sad hold milliseconds, config and
calibration SHA-256, salted clock-domain fingerprint, requester incarnation, and
up to eight peers. PREPARE has an empty roster and performs validation/warmup
without starting active hardware deadlines. Its response returns the same
identity, accepted/idempotent/error, responder node/incarnation, clock-verified,
and lifecycle state. After every peer accepts PREPARE, session sends START with
the exact eight-peer roster assembled from those responses. `RunLifecycleGuard`
rejects changed identity/config/requester/peer incarnation and makes exact
retries idempotent. Maestro activation and the 25-second active deadline begin
only at START.

Clock fingerprints are computed and compared inside participating ROS
containers from run-epoch-salted boot identity, time-namespace identity, and
time-namespace offsets. Host-shell proof equality is not required. Only
verification pass/fail belongs in published experiment evidence; raw IDs and
fingerprints do not.

`EndRun` request schema `end-run/v1` carries identity, explicit
`SUCCESS|CANCELLED|FAULT`, reason, and requester incarnation. A response binds
the same identity and reports accepted, idempotent, completed, error, terminal
outcome, `FINALIZING|COMPLETED`, artifact identity, and responder incarnation.
Accepted success is only a finalization request. Action success waits for
`PlaybackStatus.DRAINED` with final/equal counts, Maestro `RunHealth.COMPLETED`
after runtime/Home completion, and recorder completed evidence with artifact
identity. Fault/cancel remains irreversible.

The topic mapping is:

| Topic | Producer -> consumer | Type / guard |
| --- | --- | --- |
| `/alice/speech/clauses` | session -> tts | `SpeechClause`; reliable bounded; exact `SequenceGuard` plus existing `ClauseSequence` |
| `/alice/speech/pcm` | tts -> audio | `PcmChunk`; reliable bounded; `PcmStreamGuard` |
| `/alice/speech/pcm_credit` | audio -> tts | `PcmCredit`; reliable bounded; exact `SequenceGuard` plus `CreditLedger` |
| `/alice/speech/state` | audio -> expression/session | `SpeechState`; volatile best-effort keep-last-one; latest `SequenceGuard` |
| `/alice/expression/frame` | expression -> motion | `ExpressionFrame`; volatile best-effort keep-last-one; latest `SequenceGuard` |
| `/alice/face/target` | motion -> maestro | `FaceTarget`; volatile best-effort keep-last-one; latest `SequenceGuard(max_gap_ns=None)` because coalescing permits gaps |
| `/alice/run/health` | every peer -> session; audio/maestro monitor siblings | `RunHealth`; volatile best-effort keep-last-one plus local watchdog |
| `/alice/audio/playback_status` | audio -> session/maestro/recorder | `PlaybackStatus`; validated drain/fault evidence |
| `/alice/maestro/servo_receipt` | maestro -> session/recorder | `ServoReceipt`; selected controller evidence |
| `/alice/perception/face_observation` | perception -> recorder | `FaceObservation`; bounded latest observation |

All current-identity malformed data raises `ValueError` before any sequence,
sample-offset, lifecycle, or credit ledger mutation. A nonmatching identity,
including stale epoch or replaced generation, returns false/`None` and is not
adopted. `SequenceGuard` latches publisher incarnation, rejects restart
continuation, rejects future/older-than-250-ms source timestamps and backward or
duplicate sequences, and enforces a 250-ms progress gap unless explicitly
disabled for coalesced FaceTarget.

Schema identities are `speech-clause/v1`, `pcm-chunk/v1`, `pcm-credit/v1`,
`speech-state/v1`, `expression-frame/v1`, `face-target/v1`,
`run-health/v1`, `playback-status/v1`, `servo-receipt/v1`,
`blendshape-observation/v1`, `begin-run/v1`, `end-run/v1`, and
`run-speech/v1`.

## TDD evidence

Initial RED command:

```text
docker run --rm -v "$PWD:/workspace:ro" -w /workspace alice-ros2:test \
  bash -lc 'PYTHONPATH=/workspace/src:/workspace/ros2_ws/src/alice_nodes \
  pytest -q tests/ros2/test_transport.py tests/ros2/test_conversions.py'
```

Expected result before implementation: collection failed with
`ModuleNotFoundError: No module named 'alice_nodes'` and
`ModuleNotFoundError: No module named 'alice_interfaces'` (2 errors).

Additional RED checks caught missing lifecycle and credit APIs at import, and
the self-review regression reproduced restarted-publisher admission:

```text
pytest -q -p no:cacheprovider tests/ros2/test_transport.py -k restarted_publisher
FAILED: DID NOT RAISE ValueError

pytest -q -p no:cacheprovider tests/ros2/test_transport.py -k coalesced_latest
FAILED: TypeError comparing max_gap_ns=None
```

GREEN focused command used a fresh temporary colcon build/install and real
generated Python classes:

```text
colcon --log-base /tmp/alice-log build \
  --base-paths /workspace/ros2_ws/src \
  --build-base /tmp/alice-build --install-base /tmp/alice-install \
  --packages-up-to alice_nodes
source /tmp/alice-install/setup.bash
PYTHONPATH=/workspace/src:$PYTHONPATH pytest -q -p no:cacheprovider \
  tests/ros2/test_transport.py tests/ros2/test_conversions.py
40 passed in 0.22s
```

After the publisher-incarnation and coalesced-gap regression additions, the
scoped final regression reported:

```text
127 passed in 1.35s
```

That run covered `tests/contracts`, `tests/speech/test_stream_contracts.py`,
`tests/speech/test_pcm_stream.py`, and both Task 2 ROS test files after a fresh
colcon build. Pytest output was clean. Colcon emitted one benign base-image
CMake warning about its unused `CATKIN_INSTALL_INTO_PREFIX_ROOT` variable.

The lightweight image check rebuilt to `/tmp/alice-build` and
`/tmp/alice-install` in `alice-ros2:base` and imported `alice_interfaces` msg,
srv, and action modules plus `alice_nodes.transport` and
`alice_nodes.contracts`. It printed:

```text
base imports passed; optional ML absent and unloaded
```

The check asserted both Torch and MediaPipe were absent from import discovery
and `sys.modules`. No device was mounted or accessed and no runtime network API
was used. Image identities were `alice-ros2:base` image
`sha256:116c0930b0144169fd1a6c87837b898ec4881ddb40df79dc5b6f8197ed986b52`
and `alice-ros2:test` image
`sha256:39428688c46fe22d07f96905292d7aae3ebd0c4a3b0ccb276585666a19711c31`.

## Files

- `ros2_ws/src/alice_interfaces/CMakeLists.txt`, `package.xml`, `msg/*`,
  `srv/*`, and `action/*`
- `ros2_ws/src/alice_nodes/setup.py`, `setup.cfg`, `package.xml`, resource
  marker, `alice_nodes/__init__.py`, `contracts.py`, and `transport.py`
- `tests/ros2/test_transport.py` and `tests/ros2/test_conversions.py`
- `.superpowers/sdd/2026-09-15-ros2-docker-runtime/task-2-report.md`

## Commit

Implementation and tests: `a4f8169 feat: add ROS wire contracts and guards`.
This report is committed separately so it can name the immutable implementation
commit.

## Self-review

The review found and fixed two contract gaps: publisher incarnation is now
latched per stream, and coalesced FaceTarget can disable the PCM progress-gap
rule without disabling age or monotonic-sequence validation. It also confirmed
that unrelated checkpoint and untracked hardware-document changes remain
untouched and are excluded from the Task 2 commit.
