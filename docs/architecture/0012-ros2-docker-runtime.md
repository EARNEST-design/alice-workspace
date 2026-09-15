# ADR 0012: ROS 2 Docker runtime and participant lifecycle

Date: 2026-09-15. Status: implemented in the ROS runtime; physical acceptance remains separate.

## Decision

The approved ROS migration uses eight independently idle processes: session,
tts, audio, expression, motion, maestro, perception and recorder. Session admits
one action at a time and issues a fresh authoritative epoch for each admission.
All participants bind the immutable configuration/calibration identity and
clock-domain proof in PREPARE, then the exact eight-incarnation roster in START.
TTS warmup completes before Maestro activation. Restarted nodes cannot adopt
old stream authority or resume motion.

Control is latest-only, retaining the embedded original DAC frame and its
source timestamp through expression and motion. Receivers validate the first
publisher against the prepared roster; every active control hop retains the
250 ms age and progress gap. Expression, Motion and Maestro independently
observe validated PLAYING status outside their computation worker. First
control must complete within 250 ms of the original PLAYING source time; each
later completed computation/control sample renews only its original DAC source
time. Startup and prebuffering retain their separate allowance. Drain cannot
forgive missing first control, and requesting finalization does not disable
the active deadline. Reliable PCM and clauses use exact sequence and
metadata ledgers. PCM production intervals may be sparse while buffered DAC
output remains healthy; this does not waive packet age or local underflow.

Audio uses the existing fixed two-second PCM ring, 200 ms prebuffer, 20 ms
transport/callback chunks and 100 ms mouth lookahead. Credits acknowledge only
consumed TTS transport samples. Total generated output also includes the local
tail. The ten-second total-output budget reserves that tail before PCM
admission; an oversized committed response fails instead of being truncated.
The effective ROS limits are recorded beside the unchanged baseline library
configuration files.

## Ownership and cleanup

Inference and log finalization run on separate bounded dedicated workers.
Every queued job captures its immutable run identity and cancellation event;
timeout or cancellation retires queued lifecycle work before hooks execute.
An already executing hook must retire before another run can be admitted.
Terminal evidence completion and job retirement are separate conditions.
Maestro also checks revocation atomically before creating/starting its adapter.
Health runs
independently every 50 ms with a bounded shared receive depth and a per-peer
map. Local watchdogs revoke activity even when a sibling's callback or worker
blocks. PortAudio only copies PCM and signals cancellation. Maestro alone owns
the existing FaceRuntime/FaceCommandStream and trusted selected-face adapter.
The CLI and ROS participant share the extracted adapter and read-only device
identity implementation; the calibrated trajectory algorithm is not forked.

EndRun acceptance initiates asynchronous finalization. Successful EndRun seals
ordinary job admission atomically, including replacement of a queued latest
item. Retained admitted jobs finish their normal validation and work before
success cleanup can close files or snapshot model state. This retirement wait
is bounded to one second; a late error or timeout faults the run. Cancellation
interrupts the wait and retains the independent fault-evidence path. The wait
does not waive any active source/progress limit. Only a successful
current-run audio drain permits Maestro's selected post-speech pose and Home.
Session waits for runtime.done, successful Home/readback/restoration, durable
local audio and command logs, and recorder finalization before action success.
Receipt publication remains live during the serial owner's Home work. A local
fault finalizes evidence without depending on a live session or the inference
worker and never grants Home or jaw restoration. TTS monitors cancellation on
the model-owning event loop and invokes the existing bounded owned-process
terminate/join/kill cleanup even before the first PCM chunk. A blocked expression
computation cannot prevent fault evidence: no concurrent model snapshot is
attempted, late results are rejected, and a new epoch remains inadmissible until
the old computation returns. External clauses retain their admitted original
source timestamps through the Session queue and are checked again for the
250 ms age limit immediately before relay; relay never refreshes their age.

## Evidence and limitations

Each participant writes bounded derived evidence under an epoch-derived output
directory. Terminal identities hash the local evidence manifest; the recorder
also hashes shared participant artifacts and checks durable audio/servo
completion. Best-effort telemetry is explicitly not complete command evidence.
Raw PCM/images are opt-in. Replay observations and simulated PWM are not claims
of perception accuracy or physical motion.

See `docs/experiments/2026-09-15-ros2-runtime.md` for qualified images, exact
commands, dependencies, failures and validation results. Compose packaging,
installation paths, service-specific image selection and the deployment/device
matrix belong to the next task.

## Container deployment

Eight services share only an internal UDP DDS bridge and local derived-artifact
storage. Default operation is synthetic/replay with no cache or devices; tools,
headless model preview, offline model repository cache, speaker socket and
hardware mappings are explicitly selected. Every default service is non-root,
read-only, capability-dropped, and has no host IPC/network/PID or auto-restart.
The host launcher creates writable storage with the invoking user's ownership
and resolves tags to exact image IDs. Build manifests hash reviewed source
inputs independently of dirty Git state. Configuration/calibration snapshots
and original monotonic timestamps remain authoritative per run.

The speaker overlay uses the selected Pulse server socket through ALSA-only
PortAudio. Maestro alone gains host PID visibility in the hardware overlay for
meaningful existing owner checks; ambiguous fuser visibility rejects startup.
Device access is restricted to exact resolved character nodes, with read-only
by-id metadata preserving serial/interface/sysfs checks. Hardware still requires
an explicit admitted action, and the prior physical-motion issue remains open.

The user-supplied provisional description is separately documented in ADR0013.
Its optional publishers do not change the calibrated actuation identity.

## Clock proof correction after actual Compose evidence

The coordinator corrected the design's namespace-identity equality predicate
on2026-09-15. Docker 29.5+ creates private time namespaces by default, so
concurrent participants have different namespace identities despite all clocks
being unshifted. The new `host-monotonic-zero/v1` proof requires a valid identical
kernel boot identity and complete zero monotonic AND boottime offset records.
It is versioned and epoch-salted. Missing, malformed, duplicate, extra, unknown
or nonzero offset metadata, invalid boot identity, unreadable/invalid namespace
metadata, and old/incompatible proofs reject admission before preparation.
Namespace metadata remains locally readable but its identity is not hashed.

This is an inference from Linux's immutable namespace-offset semantics: the
same kernel boot with both offsets zero identifies the initial host clock even
across different namespace identities. It permits no translation, equal-nonzero
offset shortcut, fallback, extra capability or daemon configuration change.
All original250 ms source/progress/gap guards remain unchanged. The technical
correction is not a new user approval or physical qualification.

Primary sources: [Docker 29 release notes](https://docs.docker.com/engine/release-notes/29/)
and [Linux time_namespaces(7)](https://man7.org/linux/man-pages/man7/time_namespaces.7.html).
The original failed Compose run and boolean-only metadata probes remain retained
in the dated experiment artifacts; earlier sequential probes did not establish
concurrent namespace equality. Actual eight-container proof checks and focused
rejection tests are required before qualification.

### Bounded SIGTERM retirement on the qualified Lyrical executor

The qualified `rclpy` executor destroys its wake guard before joining the
multithreaded pool. A queued handler can then trigger a destroyed guard on
entry. The default signal handler also invalidates the context before local
application cleanup. Actual post-stop Compose logs exposed both cases; the
saturated-handler regression reproduces the guard ordering failure.

Alice handles SIGINT/SIGTERM by stopping new executor scheduling and calling
local `fail()` immediately. Independent fault evidence remains separate from
ordinary inference. It then stops pool submission and waits at most five
seconds for its existing threads before public executor/node/context shutdown.
This compatibility ordering uses the qualified executor's private `_executor`
and the Python pool's `_threads`; requalify it on an rclpy/Python upgrade.
If a callback does not retire, Alice writes a separate `shutdown-*.json` failure
artifact and exits with status 1 without destroying ROS entities under live
handlers or entering Python's unbounded thread-pool exit join. A process exit
cannot power off a Maestro that retains its last PWM. The production stop path
still revokes local actuation before any drain wait.

The regression covers a prepared run with saturated queued handlers and a
non-returning handler, immediate local stop, independent terminal evidence,
a live cleanup context, and bounded failure exit. This does not make arbitrary
Python or kernel calls interruptible. A storage/kernel failure can prevent
artifact persistence; the supervisor retains a final process stop boundary.
Raw upstream source/version and failed attempts are retained in the Task 4
artifact manifest. No diagnostic is suppressed or converted into success.

The exact investigated executor package is
`ros-lyrical-rclpy` version `10.0.10-1resolute.20260812.014024`. Failure exit relies on the
separate durable shutdown artifact and process status, with no stderr I/O that
could block or throw before the process boundary. The closed-stderr regression
covers this edge. Default successful teardown remains a normal exit; an active
all-node SIGTERM can deliberately report failed remote-action cleanup after
local cancellation. It must not be described as successful run completion.


## Task 4 review amendment: bound clock metadata and unavailable ROS hardware

Coordinator technical ruling, not new user approval: clock proof is now
`host-monotonic-zero/v2`. Linux exposes `timens_offsets` from
`time_ns_for_children`; the participant must read and validate both local
`/proc/self/ns/time` and `/proc/self/ns/time_for_children` links and require their
equality before interpreting the complete zero monotonic/boottime records.
Missing/malformed/unequal binding rejects before lifecycle mutation/factories.
Different participants may retain different valid namespace IDs. Version 1 and
mixed proofs reject; no timestamp translation or bound widening is introduced.
Actual participant-process evidence is distinguished from `docker exec` probes.
Primary source: https://github.com/torvalds/linux/blob/master/kernel/time/namespace.c

The current unprivileged deployment cannot prove complete host FD visibility.
Seeing host PIDs or enumerating a namespace-local `/proc` does not prove access
to every owner's FD table; empty fuser output is not proof of absence. The
coordinator chooses explicit unavailable-only ROS hardware admission until a
trusted host visibility mechanism is separately designed and qualified. All
hardware-marked BeginRun requests reject before mutation/factories, irrespective
of overlay enablement; Maestro rechecks before its hardware adapter factory.
The preparation overlay no longer grants host PID visibility. No production
privileges or host services/settings are added. The shared legacy CLI owner
helper is unchanged; simulated and speaker-only ROS paths remain available.
This supersedes the earlier ready-for-hardware-command expectation and is an
explicit live-hardware deployment gap, not completed physical acceptance.
Primary restriction: https://man7.org/linux/man-pages/man1/fuser.1.html
