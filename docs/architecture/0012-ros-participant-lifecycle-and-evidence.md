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
250 ms age and progress gap. Reliable PCM and clauses use exact sequence and
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

Inference and log finalization run on bounded dedicated workers. Health runs
independently every 50 ms with a bounded shared receive depth and a per-peer
map. Local watchdogs revoke activity even when a sibling's callback or worker
blocks. PortAudio only copies PCM and signals cancellation. Maestro alone owns
the existing FaceRuntime/FaceCommandStream and trusted selected-face adapter.
The CLI and ROS participant share the extracted adapter and read-only device
identity implementation; the calibrated trajectory algorithm is not forked.

EndRun acceptance initiates asynchronous finalization. Only a successful
current-run audio drain permits Maestro's selected post-speech pose and Home.
Session waits for runtime.done, successful Home/readback/restoration, durable
local audio and command logs, and recorder finalization before action success.
Receipt publication remains live during the serial owner's Home work. A local
fault finalizes evidence without depending on a live session and never grants
Home or jaw restoration.

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
