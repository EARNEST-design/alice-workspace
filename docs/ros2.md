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
from the actual deployment kernel metadata. The `host-monotonic-zero/v2` proof first requires valid, equal local
`time` and `time_for_children` namespace links, binding the kernel offset records
to the current participant. It then validates the same kernel boot and strictly
zero monotonic/boottime offsets; Docker 29.5+ gives
containers private namespace identities. Missing/malformed/nonzero metadata, unequal local namespace links and
incompatible proofs (including v1) reject admission. Different participants may
still have different namespace IDs. ADR 0012 records this coordinator technical
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

## Hardware configuration preparation (admission unavailable)

ROS hardware admission is currently unavailable. The coordinator's Task 4
review amendment requires fail-closed rejection until complete host ownership
visibility for both Maestro interfaces 00/02 has a separately designed and
qualified mechanism. Every hardware-marked ROS BeginRun rejects before lifecycle
mutation or factories, even with `hardware_enabled=true`; Maestro also checks
immediately before its adapter factory. This does not change the approved legacy
CLI workflow. Simulated and speaker-only ROS paths remain available.

A non-root process can see host PIDs but cannot necessarily inspect another
user's or a non-dumpable process's FD table. Namespace-local `/proc` checks cannot
prove host completeness, and empty `fuser` output cannot establish absence of
owners. The unused host-PID grant has therefore been removed. No extra capability,
privileged container, host daemon or setting change is supplied as a workaround.

The hardware overlay retains only scoped device/cache/group configuration as
preparation for future qualification. It is not an arming command. Resolve the
reviewed serial 00037376 interfaces 00/02, selected C525 capture node, read-only
model asset and numeric groups using `.env.example`, then inspect configuration:

```bash
python3 infra/ros2/deploy.py config --overlay offline --overlay audio --overlay hardware
```

Do not present `up` plus `run --hardware` as a currently supported ROS hardware
trial: admission deliberately rejects it. A future host-visibility mechanism
must prove both interfaces completely and preserve identity, ownership, cancel,
calibration and limit checks before this restriction can change.

Physical facial acceptance also remains pending: the prior camera observation
showed little motion despite changed PWM. No new servo/camera trial was run.
Neither process death nor owner revocation cuts physical controller power/PWM.
The retained scope is facial channels 6/3/4/5/9/11, jaw runtime 0/0 with successful
Home restoration 0/11, 100 ms mouth lead, 250 ms age/progress/gap guards, 25 s
active run, 10 s output, 2 s sad hold and 6 s pose deadline. This amendment changes
availability, not those limits or the unresolved operator issue.

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
