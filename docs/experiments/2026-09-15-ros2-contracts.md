# ROS 2 wire-contract qualification

Date: 2026-09-15

This note is the durable API and qualification record for the typed contracts
shared by Alice's eight ROS 2 runtime nodes. The implementation is in
`ros2_ws/src/alice_interfaces` and `ros2_ws/src/alice_nodes`.

## Run and lifecycle identity

Every active-run message carries `RunIdentity(run_id, epoch, generation_id)`.
Every stream adds `StreamHeader(identity, sequence, source_monotonic_ns,
publisher_incarnation)`. Consumers retain the first publisher incarnation for
the stream. A different incarnation cannot continue its sequence. A message
for a nonmatching identity, including an old epoch or replaced generation, is
ignored rather than adopted.

`BeginRun` schema `begin-run/v1` has two operations:

- `PREPARE` binds run identity, profile, seed, hardware flag, sad-hold bound,
  configuration and calibration hashes, salted clock proof, and requester
  incarnation. Its peer roster is empty. Nodes load and validate resources;
  TTS warms its model. Active hardware deadlines have not started.
- After all eight PREPARE responses are accepted, session sends `START` with
  the exact roster assembled from those replies. The names are `session`,
  `tts`, `audio`, `expression`, `motion`, `maestro`, `perception`, and
  `recorder`. The immutable binding rejects changed configuration or peer
  incarnations. Maestro activation and the 25-second hardware deadline begin
  only after accepted START.

Every response repeats the identity and identifies its responder node and
incarnation. `validate_begin_run_response` checks those against the request
context, validates the lifecycle (`PREPARED` for accepted PREPARE, `ACTIVE` for
accepted START), and enforces accepted/error/idempotence consistency. Accepted
hardware responses require `clock_verified=true`.

Participants compute the salted clock-domain fingerprint inside the ROS
containers from boot identity, time-namespace identity, and time-namespace
offsets. Host-shell equality is not required. Experiment publication retains
only verification pass/fail, never raw identifiers or proofs.

`EndRun` schema `end-run/v1` carries an explicit success, cancellation, or
fault outcome. Acceptance is only a finalization request. Completion requires
the response's completed state and, for success, an artifact identity. Session
does not complete `RunSpeech` until audio reports a corroborated drain, Maestro
reports completion after runtime/Home, and recorder reports completed artifact
finalization. Fault and cancellation remain irreversible.

## Action API

`/alice/run_speech` uses `RunSpeech`. Its goal selects either one basename in
the trusted fixture mount or the committed-clause source. Arbitrary paths are
rejected. `validate_run_speech_result` checks the expected run and session
incarnation, outcome enum, accepted/error relationship, and successful
artifact identity. `validate_run_speech_feedback` checks the expected run and
publisher incarnation, playback enum, the 32-clause bound, and cumulative
sample ordering.

Action feedback defines `generated_samples` as all PCM available to the audio
output, including the audio node's locally generated configured tail. Thus the
invariant is `played_samples <= submitted_samples <= generated_samples`; a
drained state requires all three counts to be equal. PCM transport credits are
separate: they acknowledge only TTS-sent samples. The local tail consumes ring
capacity through bounded local insertion and is never acknowledged as TTS
transport data.

## Topic API and delivery

| Topic | Producer -> consumer | Contract and admission |
| --- | --- | --- |
| `/alice/speech/clauses` | session -> tts | `SpeechClause`; reliable bounded; exact stream and `ClauseSequence` |
| `/alice/speech/pcm` | tts -> audio | `PcmChunk`; reliable bounded; `PcmStreamGuard` |
| `/alice/speech/pcm_credit` | audio -> tts | `PcmCredit`; reliable bounded; exact stream and `CreditLedger` |
| `/alice/speech/state` | audio -> expression/session | `SpeechState`; volatile best-effort keep-last-one; latest stream |
| `/alice/expression/frame` | expression -> motion | `ExpressionFrame`; volatile best-effort keep-last-one; latest stream |
| `/alice/face/target` | motion -> maestro | `FaceTarget`; volatile best-effort keep-last-one; latest stream with the 250 ms age and source/progress-gap limits |
| `/alice/run/health` | each peer -> session; audio/maestro monitor siblings | `RunHealth`; latest stream plus local watchdog |
| `/alice/audio/playback_status` | audio -> session/maestro/recorder | `PlaybackStatus`; validated drain/fault evidence |
| `/alice/maestro/servo_receipt` | maestro -> session/recorder | `ServoReceipt`; selected controller-write evidence |
| `/alice/perception/face_observation` | perception -> recorder | `FaceObservation`; bounded latest observation |

Latest-only streams set `SequenceGuard(exact=False)`. This permits skipped
sequence numbers but still rejects duplicate/backward sequence numbers,
future/older-than-250-ms timestamps, publisher restart, and a source-time gap
greater than 250 ms. Active FaceTarget uses that default. `max_gap_ns=None` is
reserved for a separately justified sparse source; coalescing active motion is
not such a justification. Local no-progress watchdogs remain necessary in the
runtime nodes.

PCM packets carry at most 20 ms of finite mono float data at 8--192 kHz. The
first packet for every clause embeds the complete immutable `SpeechClause`;
later packets carry its ID, sequence, and the global sample offset. The PCM
guard applies the existing `ClauseSequence` ledger at first-packet admission,
preserving unique clause IDs, strict clause order, one generation, at most 32
clauses, explicit response finality, and the cumulative 4,000-character text
budget. Clause, packet, sequence, and offset validation completes before any
ledger mutates, so a corrected packet can retry the same next sequence.

PCM credit is cumulative and generation scoped. Sent minus cumulative consumed
includes in-flight transport samples. Acknowledgement cannot exceed TTS-sent
samples, a repeated cumulative value adds no credit, and stale or changed-run
credit cannot replenish the current generation.

## Schema and semantic validation

Wire schema identities are `speech-clause/v1`, `pcm-chunk/v1`,
`pcm-credit/v1`, `speech-state/v1`, `expression-frame/v1`, `face-target/v1`,
`run-health/v1`, `playback-status/v1`, `servo-receipt/v1`,
`blendshape-observation/v1`, `begin-run/v1`, `end-run/v1`, and
`run-speech/v1`.

Converters pass speech clauses, speech state, targets, and observations through
the existing Pydantic domain contracts. Current-run malformed schemas, hashes,
dimensions, nonfinite values, status enums, PCM, ordering, and bounds raise
before state mutation. Servo receipts accept only the selected mapping:
channels 3/4/5/6/9/11 for lower eyelids, upper eyelids, forehead, jaw, left
mouth corner, and right mouth corner respectively. Receipts establish
controller write/readback evidence, not physical motion.

## Qualification

The initial implementation used real Python classes generated by ROS 2 Lyrical
under Python 3.14. The earlier implementation report recorded 127 passing scoped contract/domain tests; that historical run was not repeated under its original revision. A
lightweight-image check imported generated messages/services/actions and both
Alice ROS helper modules while Torch and MediaPipe were absent and unloaded.

The review-fix qualification commands and raw output are retained under
`artifacts/ros2/2026-09-15/` with the `task2-fix-round1-` prefix. Runs use
temporary `/tmp` build/install/log paths, a read-only workspace mount,
`--network none`, and no devices. The inherited colcon invocation emits an
unused `CATKIN_INSTALL_INTO_PREFIX_ROOT` CMake warning; Task 4 owns that build
configuration cleanup.


### Fix round 1 final evidence

The interrupted implementer reported RED duplicate-clause/budget failures and
missing reply-validator imports before coding. No raw logs for those earlier
fix attempts were retained, so they are reported history, not fresh captured
evidence. The handoff retained its partial work and added semantic edge cases.

Fresh commands and full output are saved as paired `.sh` and `.log` files in
`artifacts/ros2/2026-09-15/`:

- `task2-fix-round1-20260915-handoff-focused`: fresh colcon plus generated-class
  reply/result semantic tests. RED: `3 failed, 10 passed, 37 deselected in
  0.30s` (exit 1). Whitespace-only rejection errors and success artifact IDs
  were accepted by the partial implementation; bounded text validation fixes
  those three cases.
- `task2-fix-round1-20260915-handoff-final`: fresh colcon plus
  `tests/contracts`, `tests/speech/test_stream_contracts.py`,
  `tests/speech/test_pcm_stream.py`, and both `tests/ros2` test files.
  GREEN: `156 passed in 1.43s` (exit 0), using real generated classes.
- `task2-fix-round1-20260915-handoff-base`: fresh lightweight-image colcon and
  generated message/service/action plus helper imports. Result:
  `base imports passed; optional ML absent and unloaded` (exit 0).

Both final builds completed two packages. Build output includes the inherited
unused `CATKIN_INSTALL_INTO_PREFIX_ROOT` warning and expected setuptools
byte-compilation-disabled warnings because `PYTHONDONTWRITEBYTECODE=1` protects
the mounted source. Pytest output is clean. Scoped Ruff checks and
`git diff --check` also passed. No broader or hardware tests ran.

The exact commands, image IDs, synthetic-data provenance, configuration,
metrics and SHA-256 artifact manifest are retained in
`artifacts/ros2/2026-09-15/task2-fix-round1-20260915-handoff-manifest.json`.
The fixed test and base image IDs respectively are
`sha256:39428688c46fe22d07f96905292d7aae3ebd0c4a3b0ccb276585666a19711c31`
and `sha256:116c0930b0144169fd1a6c87837b898ec4881ddb40df79dc5b6f8197ed986b52`.

Self-review confirmed that rejected PCM metadata does not consume stream
sequence, sample offset, clause ID, or text budget; a corrected packet can
retry. Coalesced active-motion streams retain the 250 ms temporal bound.
Reply/feedback values are frozen and enforce context, lifecycle, counts and
outcome semantics. Integration still must bind those helpers to the peer
roster, apply stream/local watchdogs, insert the bounded audio tail, and
corroborate completion with playback/Home/recorder evidence.

Conclusion: the three Task 2 review findings are fixed within the shared
contract scope and the scoped regression passes. Runtime composition and
physical motion remain unqualified by this contract-only experiment.
