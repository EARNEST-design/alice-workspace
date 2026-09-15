# Alice ROS 2 Docker runtime

The default deployment is eight idle ROS 2 Lyrical participants on an internal
Docker bridge: session, TTS, audio, expression, motion, Maestro, perception and
recorder. It uses synthetic PCM, simulated DAC/servos, and no-face replay. An
explicit action starts each bounded run. No cache or device is required.

## Build and run

From the repository root, with Docker Compose v2 available:

```bash
python3 infra/ros2/deploy.py build
python3 infra/ros2/deploy.py up
python3 infra/ros2/deploy.py run
python3 infra/ros2/deploy.py down
```

`up` creates a user-owned `artifacts/ros2/local` directory before Docker sees
it, then resolves image tags to immutable IDs for both execution and evidence.
Use `--project NAME --output /absolute/directory` for isolated experiments.
`run` defaults to the reviewed positive/negative fixture, seeds 29/30. To choose
another trusted fixture or use the installed CLI:

```bash
python3 infra/ros2/deploy.py run -- ros2 run alice_nodes alice run \
  --fixture stream-visible-demo-v1.jsonl --sad-hold-ms 1500
```

Run commands inside the tools container so the clock-domain proof is computed
from the actual deployment kernel metadata. The versioned proof validates the
same kernel boot and strictly zero monotonic/boottime offsets; Docker 29.5+ gives
containers private namespace identities. Missing/malformed/nonzero metadata and
incompatible proofs reject admission. ADR 0012 records this coordinator technical
correction and its Linux/Docker evidence. No clock translation is permitted. Direct Compose
use requires a pre-created writable output directory and exact image identity
environment values; the launcher supplies them. Do not use an old image ID as
provenance for newer code.

Every default service is non-root, read-only, drops all capabilities, has no
new privileges, uses a bounded `/tmp`, and has neither device mappings nor host
IPC/network/PID. Service restart is disabled; a restarted process has a new
incarnation and cannot resume an old epoch. The healthcheck checks that the
installed local entrypoint is alive; use the actual graph/action and durable
terminal evidence for semantic health. The tools profile is explicit.

## Offline Azelma and the selected speaker

Only the public model repository cache is mounted. The Hugging Face cache root,
credentials, xet files and unrelated model repositories remain outside the
container. Offline flags are always enabled; no download fallback exists.

```bash
export ALICE_TTS_CACHE=/home/alice/.cache/huggingface/hub/models--kyutai--pocket-tts-without-voice-cloning
python3 infra/ros2/deploy.py up --overlay offline
python3 infra/ros2/deploy.py run --overlay offline
```

For the inspected host output route, the explicit audio overlay gives only the
Pulse native socket to the audio participant. The pinned ALSA-only PortAudio
uses ALSA's Pulse plugin; it has no fallback to a different device and no broad
`/dev/snd` mount. `PULSE_SINK` selects the inspected SN6140 analog sink. The
server authenticates the invoking user's UID. These variables do not change
host volume or routing:

```bash
export ALICE_PULSE_SOCKET=/run/user/1000/pulse/native
export ALICE_PULSE_SINK=alsa_output.pci-0000_75_00.6.analog-stereo
python3 infra/ros2/deploy.py up --overlay offline --overlay audio
python3 infra/ros2/deploy.py run --overlay offline --overlay audio
```

This is speaker-only with simulated Maestro/perception. Announce playback before
an attended qualification. Raw waveform retention is off by default. Use a
fresh project/output for every qualification attempt and retain failed runs.

## Explicit hardware deployment

Physical facial acceptance remains pending: the prior camera observation showed
little motion despite changed PWM. Do not start another servo run until that
concrete operator issue is resolved. The following command is prepared for the
next attended test; it has not been physically qualified by the ROS migration.

Set the numeric serial/video groups and resolve exact `/dev/ttyACM*` and
`/dev/video*` nodes for the reviewed serial00037376 interfaces00/02 and C525
capture interface. `.env.example` lists the required environment variables.
The hardware overlay grants device cgroup access only to those selected nodes.
Read-only by-id directory mounts preserve canonical symlinks; other dangling
links confer no device access. Sysfs metadata remains available for major/minor,
USB serial and interface checks. Re-enumeration requires regenerating mappings;
the runtime rejects identity mismatch.

Maestro alone uses host PID visibility so the existing `fuser` check includes
host competitors on both interfaces. No process-control capability is granted;
non-root visibility can still be restricted by host `/proc` policy. Any ambiguous
ownership result rejects startup; it must never be interpreted as an empty
host ownership check. Serial access also uses an exclusive open. The auxiliary
interface receives read-only device access for the preserved ownership/identity
check, not commands. Camera hardware remains confined to perception.

```bash
python3 infra/ros2/deploy.py up --overlay offline --overlay audio --overlay hardware
# Only after the unresolved visible-motion issue is resolved by the operator:
python3 infra/ros2/deploy.py run --overlay offline --overlay audio --overlay hardware \
  -- ros2 run alice_nodes alice run --fixture stream-visible-demo-v1.jsonl \
  --hardware --sad-hold-ms 1500
```

Both deployment hardware enablement and an explicitly hardware-marked action
are required; overlay startup alone never arms. Allowed facial channels remain
6/3/4/5/9/11, jaw runtime 0/0 with successful Home restoration 0/11,100 ms mouth
lead,250 ms age/progress/gap bounds,25 s active run,10 s total output,2 s maximum
sad hold and 6 s pose deadline. Process kill revokes software ownership but cannot
power off a physical controller, which may retain its last PWM.

## Optional provisional robot preview

```bash
python3 infra/ros2/deploy.py preview
```

The explicit `preview` profile adds separate `robot_state_publisher` and
`joint_state_publisher` containers. The user-supplied description was imported
selectively from `codex/robot-description` commit 13c2549. It retains the
`/alice_preview` namespace/frame prefix, provisional 0.62 m geometry, unmapped
body IDs, mimic joints and visual limits. Neutral visualization is not hardware
Home. No PWM bridge, actuation plugin, transmissions or invented inertia is
included. RViz and GUI sliders are optional local tools requiring their ROS
packages; they are not installed in these headless images.

## Evidence and qualification

`tests/ros2/integration_runner.py --output /absolute/new-directory` runs actual
separate-container scenarios with scoped project cleanup. Fault injection lives
only in qualification helpers, explicitly mounted into selected participants.
Timing instrumentation records the original source time at the actual Maestro
admission method. The p99 target is 20 ms;250 ms rejection bounds never widen.
Images contain a manifest hashing the reviewed source, config, hardware and
ROS inputs. Run terminals contain image/source identities, config/calibration
hashes, seeds, mode evidence, and per-node artifact hashes. Model weight hashes
are distinct from model configuration hashes. See the dated runtime experiment
for commands, outcomes, measured limits and acceptance gaps.

SIGTERM first revokes the local run and starts independent fault evidence.
Alice keeps the ROS context alive while existing executor handlers retire.
The qualified Lyrical executor requires draining its pool before destroying
wake guards. If a handler remains stuck after five seconds, the participant
writes `shutdown-*.json` and exits with status 1; inspect this alongside the
run's terminal artifacts. It is failed cleanup, never successful completion.
The launcher stops all profiles belonging to its project, including preview.
A custom `/fixtures` bind preserves the installed visible-face configuration.


The 2026-09-15 final images passed 1046 Python/ROS tests, Ruff, strict mypy and
nine host covering checks. The separate-container matrix passed 35 functional
cases plus four focused crash/retirement cases; final-image normal, SIGTERM and
speaker checks are retained separately. Offline speaker output completed with
185,760 samples and zero underflows on the selected route. Real offline
source-to-admission p99 remains 21.176 ms against the 20 ms target; this timing
gap and pending physical facial acceptance are not waived by functional passes.
