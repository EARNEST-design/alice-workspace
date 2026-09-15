# Eight-process ROS runtime qualification

Date: 2026-09-15. Worktree: `feature/streaming-affect-motion`.

Current implementation is `5ed4e23`, with final scoped re-review complete. The
dated sections below preserve earlier task evidence and identities; the final
fix-wave section records current images and production model verification.
Live ROS hardware remains unavailable pending complete host ownership proof.
Use `docs/ros2.md` for current deployment commands and scoped cache mounts.

## Implementation and scope

The eight `alice_nodes` entrypoints are `session`, `tts`, `audio`, `expression`,
`motion`, `maestro`, `perception`, and `recorder`. `alice run` is the action
client. Every process is idle until explicit admission. The runtime consumes
the generated contracts documented in `2026-09-15-ros2-contracts.md`.

The visible-face profile overlays only committed responsive-sync, visible
expression and full-blink inputs into the filenames expected by the existing
library. Baseline files and legacy CLI behavior remain unchanged. The frozen
snapshot also contains `speech/ros-runtime-limits-v1.json`: ten seconds total
output including tail, two-second ring, 200 ms prebuffer, 20 ms transport,
100 ms mouth lead, 250 ms active age/gap limits, 50 ms health, 25-second active
run, at most two seconds sad hold, and six-second pose deadline. With the
configured 300 ms tail, at most 232,800 transport samples plus 7,200 local tail
samples are admitted at 24 kHz. Oversize PCM is rejected before the PCM ledger,
timeline, ring or callback receives it; committed speech is not truncated.

Session generates a new authoritative epoch even for repeated fixture goals.
Feedback exposes it before external committed clauses can be accepted. The
external source topic is `/alice/run/committed_clauses`, using the action's
requester incarnation and returned identity. It is bounded by the existing
32-clause/4,000-character ledger and a ten-second source deadline. Fixtures must
be trusted basenames; symlink escapes and oversized files are rejected.

Participant services are `/alice/<role>/begin_run` and
`/alice/<role>/end_run`; the action is `/alice/run_speech`. Topics, schemas and
QoS follow the contract note. Health uses reliable depth 64 for eight publishers
and a bounded per-peer latest map. Actuator-control topics remain best-effort
keep-last-one; reliable streams use bounded depth 128. Exact clause admission
occurs before queued model work, preventing backpressure from aging a valid
committed clause. Original PCM/source timestamps are never refreshed.

## Deployment inputs for Task 4

Common ROS parameters (absolute paths) are `config_root=/opt/alice/config`,
`hardware_root=/opt/alice/hardware`, `output_root=/artifacts`, and
`fixtures_root=/fixtures`. Recorder requires the shared output volume to read
local durable audio/servo artifacts. `hardware_enabled=false` is an independent
deployment gate; an explicitly admitted hardware action is also necessary.
`retain_raw=false` independently controls audio/image retention.
`image_identity` and `code_identity` are deployment-provided provenance strings;
qualification passes the exact image ID and a SHA-256 digest of runtime/domain
Python sources. The literal default `unprovided` is explicit missing provenance,
not an inferred image identity. Task 4 must provide these values.

| Role | PREPARE/runtime dependency and mode |
| --- | --- |
| session | Qualified core, rclpy/generated interfaces; trusted fixtures and config |
| tts | Synthetic 24 kHz default; `tts_mode=pocket` requires speech/ML image and offline HF cache |
| audio | NumPy ring/timeline; simulated DAC default; `audio_device_enabled=true` requires sounddevice/PortAudio route; hardware action also selects actual output |
| expression | PREPARE imports ExpressionBridge: Torch and safetensors required; use qualified speech/ML image, not core |
| motion | Core plus existing composer and installed speech sync config |
| maestro | Core only; extracted trusted adapter/device identities import without Torch/MediaPipe; serial device access only after hardware START |
| perception | `perception_mode=replay` default with explicit no-face synthetic input; optional `replay_file` basename; `c525` requires qualified perception image, admitted hardware, reviewed camera selector and `model_path=/models/face_landmarker.task` |
| recorder | Core plus generated conversions; shared evidence volume |

All idle imports work in the base image, but this does not qualify expression
PREPARE in that image. No actual device was opened during this task.

The real warmup uses `PocketTtsWorker(offline=True)` with bounded internal
"Hello." synthesis and discards all warmup PCM. Mount
`/home/alice/.cache/huggingface` read-only at `/models/huggingface` and set
`HF_HOME=/models/huggingface`, `HF_HUB_OFFLINE=1`. It reports Pocket TTS 3.1.0,
`english_2026-01`, CPU, and configuration SHA-256
`e66a3783a7b8e695943b5dbc5224111ee3954cbde0d53348cb010ddfc3127c9e`.
The previously qualified model asset identities remain:

- `model.safetensors`, revision `d29db7978e464fb90cb3359ee0c69a273b9142cc`:
  `58aa704a88faad35f22c34ea1cb55c4c5629de8b8e035c6e4936e2673dc07617`.
- `tokenizer.model` at that revision:
  `d461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6`.
- Azelma embedding, revision `e81d79e8194ad4c7ce879c87a4258ef20cbf2487`:
  `c80991c79e18fe6eabb4dc053fe42a668b3f6ab5365c63a1437465af1de7f3a8`.
- C525 detector asset `face_landmarker.task`:
  `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`.
  Existing host asset:
  `/home/alice/alice-workspace/.worktrees/phase-1-passive-blendshapes/artifacts/passive-alice-face-pilot/model/face_landmarker.task`.

These weight hashes are the preceding task's read-only cache inventory, not a
new camera run. The new real-node warmup records its actual resolved model
configuration and zero run transport samples.

## Reproducible execution and evidence

All commands and raw logs have unique `task3-*` names beneath
`artifacts/ros2/2026-09-15/`; no prior failures were overwritten. Qualification
uses `alice-ros2:test` image
`sha256:39428688c46fe22d07f96905292d7aae3ebd0c4a3b0ccb276585666a19711c31`
and `alice-ros2:base`
`sha256:116c0930b0144169fd1a6c87837b898ec4881ddb40df79dc5b6f8197ed986b52`.

Use the qualified virtualenv interpreter to build entrypoints:

```bash
python -m colcon --log-base /tmp/alice-log build \
  --base-paths /workspace/ros2_ws/src \
  --build-base /tmp/alice-build --install-base /tmp/alice-install \
  --packages-up-to alice_nodes
source /tmp/alice-install/setup.bash
```

`/usr/bin/colcon` selects system Python in generated script shebangs, which lacks
the qualified venv dependencies. The first process smoke caught and retained
that failed qualification wrapper. Builds always use fresh temporary paths;
runtime tests mount sources read-only, use `--network none`, and expose no
devices. The UDP smoke uses `FASTDDS_DEFAULT_PROFILES_FILE` with the retained
`dds-probe.xml`, disabling built-in shared-memory transport. No host IPC,
privileged mode, or Docker socket is used.

The legacy CLI tests need the existing worktree's Git metadata mounted read-only
at its original absolute path and `safe.directory=/workspace`. Four wheel
build/install tests needed a disposable uv cache. The first build-provisioning
attempt failed on Docker bridge DNS; explicit build-only `--network host`
provisioning then passed. Final runtime/regression execution mounts that cache
with `UV_OFFLINE=1` and `--network none`; no runtime network allowance follows
from build provisioning.

## Results

Final full regression `task3-full-20260915-03` passed **988 tests, two skips
and two existing Python 3.14 fork warnings in 57.56 seconds**, including the
exact ten-second tail reservation and pre-admission rejection tests. The skips are the
Docker-context test inside a container without a Docker socket/CLI and optional
Node.js preview support. No device tests were enabled.

The UDP process smoke proves all eight node names, two distinct epochs,
24,000 transport samples plus 7,200 tail samples, equal generated/submitted/
played counts at drain, bounded ring depth, selected servo receipts, successful
Home and recorder manifests. A third run suspends expression for 650 ms;
250 ms health leases fault the run locally, with no Home or post-speech writes.

Initial RED tests failed on absent nodes/adapter, absent sparse-PCM constructor
support, absent autonomous fault finalization, incorrect preparation worker
ownership, stale queued clause admission, stalled DAC polling refreshing
progress, absent immediate action cancellation, and the missing total-output
budget. Scoped GREEN and the full regressions cover those behaviors.

No physical motion, audio route, camera accuracy, or Compose deployment is
claimed. Replay/synthetic inputs are authored test data with seed 29 (fixture
clauses retain seeds 29/30); there is no participant data or training/evaluation
split in this systems experiment.

## Task 3 review repair round 1

The original qualification above is retained at commit `69cf969`. Review found
missing first-control deadlines, fault evidence queued behind inference, stale
lifecycle jobs and refreshed external-clause relay timestamps. The repair adds
independent PLAYING observers for Expression/Motion/Maestro; completed control
must progress within 250 ms of original source time, including the first
sample. Drain requires prior control before Home, even for a short response.
Startup/prebuffer allowance and all transport/sample limits remain unchanged.

Every worker job captures its run identity and cancellation event. Cancelled or
timed-out queued START never invokes its adapter factory. Fault evidence runs
on a separate finalizer; `_completed` alone does not admit the next epoch while
an old computation or finalizer remains. A stalled expression call cannot
publish late or mutate a later run. TTS cancellation is monitored on its owning
asyncio loop and invokes the existing bounded owned-process stop before any
first chunk is required. External clauses preserve original time through their
queue, with another age check before relay and no sequence mutation on rejection.

`task3-r1-green-04` passed **119 focused tests in 10.29 seconds**, covering
nodes, lifecycle, generated conversions and transport. This includes a real
spawned TTS test backend blocked before its first chunk with live ACTIVE health,
blocked expression with all validated peer health live and no late proposal,
blocked-finalizer heartbeat liveness, queued/timed-out lifecycle retirement,
and 249/251 ms relay/control boundaries. Broad legacy suites were not repeated
because this repair changes no legacy implementation. Existing fork warnings
from the earlier full suite retain their disposition; inherited CMake warnings
and disabled byte-compilation notices remain visible in fresh builds.

`task3-r1-smoke-03` passed **five offline eight-process scenarios**: two
successful distinct epochs, suspended-expression failure, SpeechState remapped
away from Expression, and ExpressionFrame remapped away from Motion. Both
control-loss cases retain all eight ACTIVE heartbeats after PLAYING and fault
for original control-source progress; neither emits speech commands,
post-speech pose or Home. Their `first-control-observer.json` records contain
211 and 203 health messages respectively. Success still drains 24,000 transport
plus 7,200 tail samples with recorder manifests and Home. All process logs,
including replacement expression processes, have no traceback.

The first round smoke overlapped a focused build and its second remap scenario
faulted on a health lease instead of the intended control-progress condition.
Build contention is suspected, not proven. That failure is retained as
`task3-r1-smoke-01`; isolated `-02` and final `-03` satisfy the unchanged strict
healthy-heartbeat assertions. All runtime validation is `--network none`,
uses the same qualified image/UDP configuration and fresh temporary colcon
builds, and exposes no devices. The original evidence manifest is untouched;
`task3-r1-artifact-manifest.json` binds the repair's source and retained evidence.

The narrow real-model PREPARE check `task3-r1-warm-01` also passes offline after
the event-loop change: Pocket English-2026-01 warms successfully, stays alive,
and publishes zero run transport samples. It retains the previously qualified
read-only cache, configuration hash and model revision; this remains PREPARE
qualification, not physical or end-to-end real-model streaming acceptance.

## Task 3 review repair round 2

Rereview of `f028659` found that independent successful cleanup could overtake
ordinary work and suppress a later error after terminal success. Successful
EndRun now seals ordinary admission before a new arrival can replace a pending
latest item. Existing retained jobs finish their normal identity, sequence and
original-source-age validation and execution. The finalizer waits on a condition
for all ordinary jobs to retire without error before success snapshot/close or
terminal evidence. Errors latch before retirement is signalled.

The success-work wait is a fixed **one second**, leaving time inside the existing
five-second participant finalization deadline (fourteen seconds for Maestro).
It is not a ROS parameter or an extension of the active 250 ms source/progress
limits. Timeout converts pending success into fault. Cancellation interrupts
the wait immediately, retaining independent fault evidence for a stuck worker;
the next epoch remains barred until prior jobs and finalization retire. No
protocol, hardware, model-dependency or other runtime-parameter changes follow.

`task3-r2-red-01` reproduced four ordering failures (120 other tests passed).
`task3-r2-green-01` passed **124 tests in 12.26 seconds**, covering generated
conversions, transport and runtime/lifecycle behavior. The new tests prove
admission sealing without latest-item replacement, no premature success cleanup,
a retained late error, bounded fault evidence despite a stuck job, immediate
cancellation of a pending success wait, and two actual Recorder event writes
persisting before its file closes. Existing heartbeat, first-control and queued
START cancellation regressions remain passing.

`task3-r2-smoke-01` passed the five isolated offline eight-process scenarios:
two successful epochs with complete 31,200-sample output, sibling suspension,
and each first-control topic suppressed while all eight ACTIVE incarnations
remain live. The SpeechState/ExpressionFrame observer artifacts retain 213/211
health messages. Fault cases have no Home/post-speech writes, and every process
log, including replacements, has no traceback. No build overlapped the timing
smoke. Ruff and diff whitespace checks pass. Inherited warning dispositions
are unchanged, and no broad legacy suite was repeated for this base-runtime-only
repair. `task3-r2-artifact-manifest.json` separately binds all round-two evidence
and source hashes; both earlier qualification manifests remain unchanged.

## Task 4: actual Compose deployment

The migration now packages eight separate default participants plus explicit
`tools` and headless `preview` profiles. Default boot is idle, non-root,
read-only, capability-dropped and confined to an internal UDP bridge, with no
model caches or device mappings. Application source and the large pinned Python
dependency stages are separate. Builds use the qualified venv interpreter for
colcon and verify installed entrypoint shebangs. Additional OS/ROS packages are
pinned; source changes reuse the large wheel layers (small final OS runtime
layers may rebuild). Images and each qualification helper snapshot have
independent SHA256 identities; an old Git commit is not used to identify dirty
application source.

Robot description files were selectively imported from
`13c25490d78ccec05a6f2714deafb5c39c60862b`. Lyrical Xacro/URDF topology and real
separate-container joint-state/TF checks preserve the `/alice_preview` prefix,
provisional 0.62 m height, body IDs and mimics. There is no PWM bridge or new
actuation scope. The imported package's obsolete CMake minimum was corrected;
the remaining `CATKIN_INSTALL_INTO_PREFIX_ROOT` unused-variable warning comes
from colcon's generic CMake invocation for an ament package, not a failed build.
It is retained rather than suppressed. The first build used an incorrect parser
package name; `ros-lyrical-urdfdom-py` is the released Lyrical package.

### Clock-domain deployment correction

The first live graph rejected PREPARE because Docker 29.7.2 gives concurrent
containers separate time namespaces. Earlier sequential probes had not exposed
this distinction. Concurrent participants have the same kernel boot and complete
zero monotonic/boottime offsets. Under the coordinator's explicit technical
ruling, proof algorithm `host-monotonic-zero/v1` validates those metadata and
hashes the version, run epoch and kernel boot identity. Namespace metadata must
still be readable and well-formed; its inode is diagnostic rather than part of
the proof. Missing, malformed, duplicate, extra, unknown or nonzero offsets and
incompatible proofs reject admission before factories. All 250 ms source/progress
limits remain unchanged, with no timestamp translation or daemon change.
Raw clock IDs/proofs are not retained. All-eight concurrent proof evidence is
recorded inside each actual Compose scenario; host-shell proofs are not used.
See ADR 0012 and the preserved clock failures/probes for the Linux/Docker basis.

### Functional and timing observations

`task4-compose-smoke-02` passed the uninstrumented default graph, synthetic
success with admission timing, and headless preview. Synthetic output contains
24,000 transport samples plus 7,200 tail samples: generated=submitted=played
31,200, zero underflow, bounded ring, successful Home and recorder manifest.
The 61 measured admissions have p99/max 16.98295 ms, meeting the 20 ms benchmark
in that run without competing builds.

`task4-compose-matrix-01` passed20 functional scenarios, including real offline
Azelma through the graph, cancellation, gap/duplicate/stale PCM, delayed
expression, first SpeechState loss, credit backpressure, controller/recorder
fault, config/clock mismatch, cancel during sad hold, five owner crashes and
expression restart. The offline output has 178,560 transport plus 7,200 tail
samples:185,760 generated=submitted=played (7.74s), zero underflow and48,000
maximum ring capacity. Exact model/tokenizer/Azelma assets were verified inside
the actual TTS container before inference. This is real model streaming, beyond
the earlier Task 3 PREPARE-only warmup. It preserves the accepted Azelma seeds,
visible-face tuning, sample accounting and bounds; it is not a bit-identical
waveform or physical baseline claim.

Real-model timing does **not** meet the 20 ms p99 target. Matrix01's373 admissions
have p50=3.658ms,p95=15.019ms,p99=22.326ms,max28.566ms, four above 20 ms.
A focused repeat (`task4-focused-matrix-02/offline-profile`) has 372 admissions,
p99=21.760ms,max32.293ms, seven above 20 ms. All remain below the unchanged 250 ms
rejection bound. Matched source timestamps locate tails across expression
execution and later scheduling waits; motion execution p99 is0.610ms.
None of 21 retained healthcheck execution intervals overlaps those seven tails.
That diagnostic wall/monotonic correlation does not establish CPU causality.
The audio wrapper sampled the prior `last_dac_ns` before the method updated it,
so it cannot attribute newly published audio source age. Actual audio polling
already occurs every2 ms;20 ms controls PCM chunks and progress spacing. No
unmeasured cadence/resource change was made, and functional pass is not timing
acceptance. Coordinator stage analysis and healthcheck attribution scripts bind
their retained inputs by hash.

The original drop-PCM `underflow` case safely aborted on missing mouth lookahead
before callback drain, so it is not callback-underflow proof. The focused repeat
adds an actual callback output-underflow status injection: counter 1, immediate
local synchronization fault and no successful drain. Source-exhaustion initially
failed in the helper because `/fixtures` was a symlink into installed config;
a custom bind hid visible-face resources. A real directory fixes that packaging
boundary, verified by an installed-image RED→GREEN mount test and the subsequent
actual source-exhaustion fault (`source closed without end_of_response`).

### Teardown qualification and evidence limits

Early matrix logs were captured before Compose stop and cannot prove clean
teardown. Capturing logs after stop exposed premature ROS-context destruction,
then queued-handler wake-guard destruction in the Lyrical executor. The focused
SIGTERM tests retain both RED stages. A saturated-handler test proves clean
retirement; a non-returning handler proves local stop within250 ms, independent
fault evidence and failed process exit after a five-second drain deadline.
No exception is suppressed. A separate shutdown failure artifact preserves
failed cleanup without rewriting a sealed run terminal.

`task4-boundary-matrix-03` adds real bridge cases for both first-control losses,
blocked TTS subprocess/expression cancellation, external relay aging, stale
epoch after fresh PREPARE, conflicting/idempotent terminals, queued START and
success retirement late-error/timeout. The latter three target one actual
participant through its lifecycle services while seven other containers remain
idle; they are not represented as all-eight-active action tests. The expression
observer initially requested incompatible reliable QoS, so its no-late-frame
assertion is not passing coverage until rerun with the actual best-effort topic.
The first active SIGTERM-all run left local terminals but session required the
supervisor's15 s SIGKILL while waiting on stopped peers; the explicit bounded
process-exit repair addresses this separately from clean post-run shutdown.

Final commands/results, speaker-only outcome, exact evidence-level coverage and
artifact manifest are recorded below after final verification. Servo/camera
physical acceptance remains pending; metadata-only hardware overlay inspection
does not resolve the previous visible-motion issue.

### Acceptance-row mapping

The final matrix uses eight actual runtime containers on the inspected internal
bridge and a ninth tools client. Selected test-only wrappers inject faults into
those processes. They are not host-unit or same-container substitutes.

| Acceptance input | Actual-container scenario / evidence |
| --- | --- |
| Default boot; happy simulation | `default`, `success`: live graph/inspect, idle assertion, matched sample accounting, mock receipts/Home, recorder manifest |
| Real TTS | `offline`: actual cached Azelma inference, in-container model asset hashes and model identity, complete sample counts |
| Bounded PCM | `backpressure`: slow DAC, cumulative credit duplicates, outstanding reservations≤48,000, producer cancel |
| PCM gap/order | `gap`, `duplicate`: fresh packets with missing/duplicate sequence rejected by audio |
| Stale source | `stale`, `delayed-expression`: expired original source rejects current work |
| First-control loss | `first-control`, `first-control-motion`: each topic withheld while all eight ACTIVE publishers remain observed |
| Stalled inference cancellation | `tts-stall-cancel`, `expression-stall-cancel`: actual owned subprocess termination; local terminal before blocked expression retires; matching QoS observes zero late frames |
| Queued START | `queued-start`: cross-container lifecycle service, no factory call, old job blocks new epoch until retirement |
| External relay aging | `external-relay-aging`: original stamp unchanged, delayed forwarding rejected, zero PCM |
| Generation change | `stale-epoch`: prepare fresh audio epoch, publish actual old packet, no generated/submitted/played samples in new epoch |
| Session/audio/expression/Maestro/recorder crash | `kill-session`, `kill-audio`, `kill-expression`, `kill-maestro`, `kill-recorder`: scoped SIGKILL, independent surviving evidence, last-write stop timing |
| Callback underflow | `callback-underflow`: inject actual callback status error, counter 1/no drain; `underflow` separately retains earlier safe lookahead rejection |
| Controller error | `controller`: mocked owner error propagates through local watchdog; no Home/recovery |
| Cancel during sad hold | `cancel-hold`: cancellation after audio drain wins before Home |
| Successful finalization ordering | `success-retirement-error`, `success-retirement-timeout`: admitted work blocks success; late error/deadline faults; independent finalization and epoch retirement |
| Conflicting terminals | `terminal-conflict`: repeated FAULT is idempotent; repeated SUCCESS rejected; terminal digest unchanged |
| Config mismatch | `config-mismatch`: mismatched request digest rejects preparation; malformed calibration/model/profile combinations retain focused contract/unit coverage |
| Clock mismatch | `clock-mismatch`: incompatible participant proof rejects PREPARE; strict metadata parsing/equality matrix is unit-tested and all actual containers prove zero offsets/equal versioned proof |
| Process restart | `restart-expression`: new incarnation cannot resume the old run |
| Face-observation failure | Every successful replay run: explicit no-face validity/error, empty scores, no inferred affect |
| Source exhaustion | `source-exhaustion`: incomplete fixture faults without successful completion/Home |
| Transport timing | Original source→actual Maestro admission rows; synthetic and real-model distributions retained separately; unmet20 ms target explicit |
| SIGTERM teardown (additional) | `sigterm-all`: local cancellation/evidence before bounded handler-drain failure; ordinary post-run stops exit cleanly |
| Robot description (additional) | `preview`: separate joint/robot-state publishers, synthetic joints and prefixed TF, provisional geometry only |

The three targeted lifecycle-service cases deliberately leave the other seven
participants idle. Metadata parsing rejection tests cannot safely manufacture
nonzero kernel offsets inside hardened containers; their evidence level remains
unit tests plus actual-container valid-domain proof and incompatible-proof
admission rejection. No shifted-clock or physical-hardware qualification is
implied. All final post-stop logs are retained, including expected injected
faults and explicitly failed cleanup.

### Final matrix and verification results

`task4-final-matrix-01` qualifies build 07's exact image/source identities:
**35/35 functional scenarios pass**. The read-only evidence audit verifies every
sealed terminal file hash, audio count/ring bounds, eight observed ACTIVE
publishers in the stall/topic-loss cases, and all post-stop logs. The eight
runtime participants stop with exit 0 after completed runs. Deliberate owner
SIGKILL exits 137; preview `ros2 launch` processes terminate with 143 while their
publishers report clean exit. Active SIGTERM-all gives the session exit 1 and a
separate five-second failed-quiescence artifact with cancellation and completed
local terminal evidence; the seven other runtime participants exit 0. This is
bounded failed cleanup, not successful shutdown or hidden supervisor SIGKILL.
No Traceback, RCLError or Destroyable diagnostics remain in these stopped logs.

The final build 07 synthetic 64 admissions have p99/max 15.837108 ms. Its real
offline 385 admissions have p99=21.175648 ms,max23.221253 ms; the 20 ms target remains
unmet. All three real-model distributions are retained, and no competing build
ran during their timing samples. The offline run still has exact sample
accounting, zero underflow, independent Home and recorder evidence.

`task4-final-supplement-01` passes 4/4 additional scenarios: actual Recorder
retirement with a late error/timeout, and session/audio self-crash through a
test-only ROS trigger. Each process writes its own CLOCK_MONOTONIC marker and
immediately sends SIGKILL to itself. The helper refuses to crash if writing the
marker takes more than 2 ms; neither run rejected. Marker→last retained mock
receipt is 207.914402 ms (session) and 209.117 ms (audio). Those intervals include
marker writing and any subsequent scheduling; they avoid Docker CLI startup.
They do not prove physical PWM cutoff or a general hard real-time guarantee.

The coarse host-command-start→last-receipt measurements remain 297.848 ms (session),
308.497 ms (audio),322.931 ms (expression),330.107 ms (recorder). Docker CLI duration
alone is164–214 ms. Tools-observed last target heartbeat→last receipt reaches
255.681/255.492/251.952/257.770 ms respectively; this observer is not the exact
heartbeat consumed by Maestro, and watchdog/in-flight scheduling is visible.
These measured gaps are not relabeled as an exact250 ms end-to-end stop. The
strict expiry/admission predicates remain 250 ms. `task4-stop-analysis.json`
separates command start/completion, diagnostic Docker-exit conversion, observed
heartbeat and self-marker measurements with input hashes and limits. A killed
Maestro cannot finalize its local command log; Recorder retains 18 published
servo receipts, which need not include its final write. Process death prevents
future writes by that owner but cannot remove physical controller power.

The first full verification passes 1046 tests with three skips and two existing
fork warnings in 77.29 s; Ruff passes. Strict mypy then fails 15 errors in five
files, so the combined command is a failed attempt. The exact pre-migration
`3f4093b` source passes 85 files under the same image/checker. The migration's
lightweight helper extraction had dropped typed signatures and explicit
re-exports. Those are restored; `types-PyYAML==6.0.12.20260906` is declared in
the development lock and pinned test image, and now-obsolete YAML import ignores
are removed. These changes affect annotations/re-exports, not control behavior.
Host strict mypy passes 87 files; the final image verifies the installed stubs.
The final covering script uses `bash -eo pipefail` inside Docker so an earlier
pytest/Ruff failure cannot be masked by a later successful command.

The three container skips are the Docker effective-context regression, the
installed-image custom-fixture bind regression (Docker CLI/daemon deliberately
absent inside the test container), and the optional Node.js speech-preview
clock/seek regression. They are run separately on the host. Both fork warnings
come from existing deliberate hardware-authority fork tests; no new callback
cleanup diagnostics accompany the suite.

Final self-review also caught a closed-stderr edge in failed process exit. A
diagnostic write could raise before `os._exit` and enter Python's unbounded
pool join. The final version relies on its durable failure artifact and exit 1,
with no potentially blocked diagnostic I/O before that boundary. The saturation,
stuck-handler and closed-stderr prepared-run tests pass 3/3 in 13.16 s and assert
local stop within250 ms plus independent fault evidence. Build 08 includes this
narrow fix and the type-only repairs. Build 07's broad matrix is preserved with
its exact identities; the final image receives fresh normal/SIGTERM/speaker
checks and full regression rather than relabeling earlier binaries.


### Final build 08 handoff

Build 08 completed successfully. Its core, speech, perception and test images
share source-tree SHA256
`6390a6263c19b5642b60a3cd9310fe98caacbbcf5d2503b711500057f8b230c1`.
`task4-image-source-manifests-08.json` records each immutable image ID and every
input file hash; `task4-package-inventory-08-*.txt` records installed packages.
`task4-full-verification-02.sh` exits 0: 1046 passed, three skips, two existing
fork warnings in 76.68 s; Ruff passes and strict mypy passes 87 source files.
`task4-host-covering-01.log` has nine passes in 1.53 s, covering all three skips.

`task4-final-image08` passes the normal and active SIGTERM-all scenarios using
these exact images. Post-stop audit confirms the same explicit bounded failed
session cleanup (exit 1, local terminal complete, cancellation requested,
five-second quiescence deadline); ordinary completion stops cleanly.
`task4-public-launcher-08.sh` exercises the documented launcher up/run/down and
exits 0. All eight sealed terminals report success, 31,200 samples are played,
and post-stop logs have no ROS teardown errors. The stack is removed; this
launcher probe did not separately retain per-container exit codes.

After advance playback announcement, `task4-speaker-08` passes real offline
Azelma through the selected SN6140 analog Pulse sink. Only the audio container
receives the Pulse socket; Maestro and perception remain simulated. The audio
clock is `portaudio-dac`: 178,560 transport samples plus 7,200 tail samples give
185,760 generated/submitted/played samples, drained=true, zero underflows and
48,000 maximum ring depth. Model, tokenizer and Azelma bytes are verified inside
TTS before inference (`speaker/model-assets.json`). Simulated Home completes;
physical_motion_verified remains false. All eight post-stop exits are 0, sealed
artifact hashes verify, and stopped logs have no ROS teardown exceptions.
This verifies stream completion on the selected route; it is not an independent
acoustic measurement or a user report of audibility. No waveform is retained.

`task4-artifact-manifest.json` hashes successful and failed attempts, retained
harnesses, source/model manifests and coordinator clock/timing rulings. Build
07 broad-matrix evidence is explicitly distinct from build 08 targeted evidence.
No new servo or camera trial ran. Whole-migration review and physical acceptance
remain coordinator/operator follow-up; the real-model 20 ms p99 gap is open.


### Task 4 review fix round 1

The review found two admission gaps. Clock v1 validated zero offsets without
binding them to the current namespace, although Linux reads `timens_offsets`
from `time_ns_for_children`. Clock v2 requires valid, equal local current/child
namespace links before interpreting those records; different participants may
still have different IDs. Missing/malformed/unequal binding and v1 proofs reject
before lifecycle mutation or factories. Initial focused RED has 30 failures
(including the changed required API argument); GREEN has 61 passes. Subsequent
covering includes the added ownership tests.

A no-device hidden-owner probe uses two regular files representing interfaces
00/02, a non-dumpable owner and an unprivileged checker in an isolated,
capability-dropped container. The owner's FD table is inaccessible to the checker;
`fuser` returns 1 with empty stdout/stderr. Old image 08 admits the hardware-marked
request and mutates identity (`task4-r1-hidden-owner-red-02.log`). The first probe
attempt failed before admission because ROS logging targeted a read-only home;
that harness failure remains `-red-01.log`. The corrected probe uses a writable
local log directory. No serial/camera device is mapped or opened.

Per coordinator ruling, live ROS hardware admission is explicitly unavailable
until a trusted complete host visibility mechanism is separately designed and
qualified. This is an availability limitation, not a newly invented positive
proof. Every hardware-marked BeginRun rejects before mutation/factories, even
when hardware is enabled, and Maestro rechecks immediately before its factory.
The hardware preparation overlay drops its unnecessary host-PID grant. No
production privileges, host service or host setting changes are introduced.
Shared legacy CLI behavior and simulated/speaker-only operation remain unchanged.
The initial owner tests fail 3/3 before the fix. Final hidden-owner GREEN shows
accepted=false, identity_mutated=false and zero prepare/factory calls, despite
the same misleading empty fuser result.

The final round-1 images share source SHA256
`c4a4a56755d81d9f507d09712b36576f7cba3b0fc926c93ac76b70d544eb8835`;
`task4-r1-image-source-manifests.json` identifies each immutable image and exact
input hash. `task4-r1-container-01` passes three actual-container scenarios:
ordinary default success, eight participant-process valid-binding proofs, and
Maestro's mismatched-child binding rejection. Test-only instrumentation records
booleans in the actual participants' admission calls, with no raw IDs/proofs;
separate docker-exec probes are explicitly labeled as child-process evidence.
All retained post-stop logs and terminal hashes pass the three-set audit. No
Maestro commands or lifecycle directory exist for rejected binding admission.

The first covering run had six legacy tests fail because its harness omitted
worktree Git metadata; no production fix was made for that. Corrected covering
passes 150 tests with one host-Docker skip and two existing fork warnings, plus
Ruff and 87-file mypy. Seven host Compose checks cover the skip. Final pinned-image
covering also passes 150 tests in 25.29 s, Ruff and 87-file mypy
(`task4-r1-covering-final.log`); the artifact manifest retains
all RED/GREEN and failed harness attempts. The original 35-case matrix and real
speaker evidence remain independently identified; no unchanged broad matrix or
new speaker/servo/camera trial was repeated for these admission-only changes.
The real-model timing target and physical acceptance limitations remain open.

### Final migration fix wave

Whole-migration review identified invalid admission flags on cancelled actions,
a missing first-DAC deadline and model checksums verified only by qualification
helpers. Commit `5ed4e23` fixes all three and distinguishes older evidence in the
runbook. Accepted actions keep accepted=true through cancellation or runtime
faults; genuine pre-admission rejection remains separate. Audio arms an
independent 250 ms first-DAC deadline after prebuffer completion at device startup,
without inventing a source timestamp. Production Pocket PREPARE verifies the
approved model, tokenizer and Azelma bytes before worker construction. Run
evidence records that loaded worker's asset identity, even if cache contents
later change; a dead worker cannot silently reload unchecked bytes.

All four final role images share source SHA256
`f7b99195cbd2ae7d72a7f097f88e549ef47654abd268a52db60045f38efa573a`.
`final-fix-image-source-manifests.json` records immutable image IDs and per-file
hashes. `final-fix-full-01.sh` uses the exact test image, read-only sources/Git
metadata and an offline cache with an inner strict shell. It exits 0 with
1,079 tests passed, three skips, two existing fork warnings in 78.95 s;
Ruff passes and mypy passes 87 files. `final-fix-host-01.log` records nine
covering passes, including all three container skips. No production image
input changed after this verification.

The focused actual-container matrix retains both attempts. Container01 passes
seven cases: default success; cancellation during preparation, playback and
finalization; first-DAC loss; checksum mismatch; stalled-TTS cancellation.
Its offline helper fails before inference because docker exec bypasses ROS
entrypoint setup. The corrected invocation runs only offline in container02,
on unchanged images. Every complete action result is validated and retained,
including accepted, run/epoch/generation, responder, outcome and artifact.
All eight passed cases have eight post-stop service exits of zero and no ROS
teardown exceptions. The original helper failure remains visible.

First-DAC injection opens/starts a test stream that never calls back. The local
fault is `first DAC progress expired`, with zero submitted samples and no DAC
source timestamp. The 250 ms expiration predicate is observed by the existing
20 ms watchdog after 259.033 ms; this does not claim a physical stop within250 ms.
The checksum-mismatch run rejects PREPARE before its worker-constructor sentinel
or model evidence appears. Focused tests also cover missing/escaping assets,
healthy first/subsequent DAC progress and warm/dead worker cache changes.

Real offline Azelma produces 178,560 transport samples plus 7,200 tail samples:
185,760 generated/submitted/played, drained=true, zero underflows, maximum ring
48,000, simulated DAC. Production `tts/model.json` includes the three approved
asset path/hash/size identities. Its SHA256
`aff1d6272e2b57f879400cbcb9781893262b625d9b0c9733fc72c2048f0bcc73`
is in both TTS terminal and recorder manifest. The returned artifact
`eef084eedbebded42c75d3f91e381054e0ddfc72cdfccb300ce8924c904efe74`
hashes the recorder terminal, which binds its manifest. The first independent
audit incorrectly expected the manifest hash directly; its failed script/log
remain alongside the corrected successful audit.

This single offline run has 383 admissions, p99=16.964663 ms and
max=21.683351 ms. Earlier runs exceeded the 20 ms p99 target; no causal
performance fix or repeatable timing qualification is claimed. No speaker,
servo or camera trial ran during these repairs. Build08 speaker evidence is
separate, and physical facial/PWM cutoff acceptance remains pending.

`final-fix-artifact-manifest.json` binds commit `5ed4e23`, exact image/source
identities, the preceding fix manifest and 1,927 evidence files (8,714,687 bytes),
including failed attempts. Coordinator verification independently checks those
hashes in `coordinator-final-fix-manifest-verification.json`. Existing fork/CMake
warning debt and live ROS hardware unavailability remain explicitly disclosed.

The sole scoped re-review closed I1–I3 and the runbook chronology correction,
with no new breakage. All required implementation review findings are closed;
the existing timing/hardware/physical acceptance limitations remain open.
Review reports, all six rulings, briefs, diffs and the final progress ledger are
preserved in `artifacts/ros2/2026-09-15/sdd-final-review/` with a verified archive
manifest; only this plan's redundant scratch directory is removed. The branch,
worktree, earlier manifests and local experiment artifacts remain intact.
