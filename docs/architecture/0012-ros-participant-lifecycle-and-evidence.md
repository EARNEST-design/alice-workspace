# ADR 0012: ROS participant lifecycle and terminal evidence

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
