# Eight-process ROS runtime qualification

Date: 2026-09-15. Worktree: `feature/streaming-affect-motion`.

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
