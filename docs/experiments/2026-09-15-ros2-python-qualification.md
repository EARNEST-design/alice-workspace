# ROS 2 Lyrical Python 3.14 qualification

Date: 2026-09-15
Result: qualified for the containerized, hardware-free Python workloads in this
repository

## Objective

Qualify the existing locked Alice dependencies with ROS 2 Lyrical's system
Python 3.14 before widening the package metadata, and create reproducible image
targets that keep lightweight runtime code separate from speech and perception.
No camera, speaker, serial controller, or other device was exposed during this
work.

## Inputs and provenance

- Source baseline: `4973611` in the preserved
  `feature/streaming-affect-motion` worktree.
- ROS base:
  `ros:lyrical-ros-base-resolute@sha256:dbb2a254523ee3c40ec9fc07956bc1042253c7beb3b6dad4a4585d85c99e9716`.
- Container interpreter: CPython 3.14.4; `rclpy` loaded from
  `/opt/ros/lyrical/lib/python3.14/site-packages`.
- Pocket TTS model: package 3.1.0, built-in `english_2026-01` model, preset
  voice `azelma`, loaded from the pre-existing read-only host cache at
  `/home/alice/.cache/huggingface` with `HF_HUB_OFFLINE=1`. The resolved
  model/tokenizer snapshot was `d29db7978e464fb90cb3359ee0c69a273b9142cc`
  and the voice-embedding snapshot was
  `e81d79e8194ad4c7ce879c87a4258ef20cbf2487`.
- MediaPipe model: existing
  `/home/alice/alice-workspace/.worktrees/phase-1-passive-blendshapes/artifacts/passive-alice-face-pilot/model/face_landmarker.task`,
  SHA-256
  `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`,
  mounted read-only for qualification.
- The direct application requirements were retained: Pocket TTS 3.1.0,
  sounddevice 0.5.6, safetensors >=0.8.0, Torch >=2.14.0, Pydantic >=2,<3,
  and pyserial >=3.5,<4. The refreshed lock resolved the same direct package
  versions used by the prior lock, including CPU Torch 2.14.0, MediaPipe 1.0.1,
  OpenCV contrib 5.0.0.93, and NumPy 2.5.2.

## Test-first metadata change

`tests/ros2/test_packaging.py` was added before the metadata change. The first
semantic run reported `2 failed, 1 passed`: Python 3.14 was outside the wheel's
`Requires-Python` range and the complete-install extra was absent. The
lightweight `FaceRuntime` subprocess import already passed without loading
Torch or MediaPipe. After changing the metadata and refreshing the lock, the
same test reported `3 passed`; the combined new and existing wheel packaging
tests reported `5 passed`.

The accepted metadata now supports `>=3.12,<3.15`, moves MediaPipe and OpenCV
into a `perception` extra, includes perception dependencies in the development
group, and supplies `full` as the complete legacy installation path. The
existing installed-wheel CLI check explicitly installs the `perception` extra.

## Disposable compatibility probe

The initial probe copied the repository into `/tmp/alice-probe` inside a
disposable container and changed only that copy's Python constraint. This kept
the checked-in constraint unchanged until qualification succeeded. The probe
then created `/opt/alice/venv` with `--system-site-packages` so the same
interpreter could import ROS's `rclpy` and Alice dependencies.

The exact compatibility failures, in discovery order, were:

1. Sourcing `/opt/ros/lyrical/setup.bash` under Bash `nounset` failed on the
   unset `AMENT_TRACE_SETUP_FILES` variable. The entrypoint therefore uses
   `set -eo pipefail`, sources ROS and the optional Alice workspace, then
   `exec`s its argument.
2. MediaPipe's OpenCV import needed `libGL.so.1`.
3. Ubuntu's PortAudio library enabled its PulseAudio backend. Importing
   MediaPipe transitively imported sounddevice, and PortAudio initialization
   failed because no PulseAudio server was present. There is no supported
   environment switch in this PortAudio backend. The image builds checksum
   verified PortAudio 19.7.0 with ALSA enabled and PulseAudio absent instead.
4. Opening the real MediaPipe model then exposed missing `libEGL.so.1`, followed
   by `libGLESv2.so.2`. An `ldd` inspection of MediaPipe's binding and OpenCV's
   native extension bounded the remaining runtime-library set. The perception
   image installs the pinned Ubuntu `libegl1`, `libgl1`, and `libgles2`
   packages.

The first two exploratory logs were overwritten while the dependency boundary
was being narrowed, so they are summarized here and are not claimed as retained
raw evidence. Subsequent failures are retained separately:

- `artifacts/ros2/2026-09-15/python314-probe-portaudio-alsa-libegl-failure.log`
- `artifacts/ros2/2026-09-15/python314-probe-libegl.log`
- `artifacts/ros2/2026-09-15/python314-probe-full-system-libs.log`

The successful probe imported all locked dependencies, opened the real
face-landmarker model, and performed offline Azelma inference: 24,000 Hz,
24,960 finite samples, peak absolute amplitude 0.550262.

## Image construction

`infra/ros2/Dockerfile` creates these pinned targets and paths:

| Target | Purpose | Image ID from this run |
| --- | --- | --- |
| `core` / `alice-ros2:base` | ROS, lightweight Alice dependencies and source | `sha256:116c0930b0144169fd1a6c87837b898ec4881ddb40df79dc5b6f8197ed986b52` |
| `speech` | CPU Torch, Pocket TTS, sounddevice and ALSA-only PortAudio | `sha256:acee260a2044c2518da135400ac348ae8107fdc6ae049a545772dbc048f685bd` |
| `perception` | MediaPipe, OpenCV and required GL libraries | `sha256:2a118231dbfacd3d3c90b708741f064ec941913abaf3bf89d810a9b24d18e806` |
| `test` | Full dependency and repository-test environment | `sha256:39428688c46fe22d07f96905292d7aae3ebd0c4a3b0ccb276585666a19711c31` |

All targets contain `/opt/alice/config`, `/opt/alice/hardware`,
`/opt/alice/src`, and the system-compatible `/opt/alice/venv`. The speech and
test requirements bind Linux amd64 CPU Torch to the exact CPython 3.14 wheel
URL and SHA-256 rather than using a global secondary package index. Direct apt
dependencies and the PortAudio source archive are version/checksum pinned.
The repository-root Docker context starts from a catch-all exclusion. It
re-includes dependency metadata, source, tests, ROS/container infrastructure,
and an exact list of reviewed configuration and hardware files. New files under
`config` or `hardware` remain excluded until explicitly reviewed and listed.
Credentials, caches, artifacts, local calibration, model binaries, and build
output remain excluded even under an otherwise allowed source directory.

## Verification

The repository suite ran with the built test image's Python 3.14 interpreter
against a read-only mount of this trusted worktree. The final process-local Git
configuration allowed `safe.directory` only for that mount and disabled pytest
cache writes:

```text
881 passed, 1 skipped, 2 warnings in 78.65s
```

The skip is the optional Node-based speech-preview regression. Both warnings
are the existing Python 3.14 fork-from-multithreaded-process warnings in the
hardware identification tests. Earlier retained runs document why the
read-only repository mount and exact safe-directory setting were necessary:
the copied image context lacked the repository's `tools` directory and Git
metadata, then Git rejected the mounted worktree's ownership. These were
qualification-wrapper failures, not dependency failures.

Installed-image smoke runs remained separate from that repository-mounted test
evidence. With `--network none`, read-only model caches, and no device mounts:

- the base image imported `rclpy` and `FaceRuntime` without loading Torch or
  MediaPipe;
- the perception image opened the real face-landmarker model;
- the final speech image synthesized 13,440 finite Azelma samples at 24,000 Hz;
- the final test image opened the real face model and repeated the same offline
  synthesis.

Build, probe, suite, and smoke output is retained under
`artifacts/ros2/2026-09-15/`, including
`docker-build-*-fix-round1.log`, `image-packaging-fix-round1.log`,
`python314-full-suite-qualified.log`, and `image-qualification-final.log`.
An initial final-smoke command used the wrong local `AudioClip` attribute after
successful inference; that command failure is retained separately as
`image-qualification-final-attribute-failure.log` and was corrected to inspect
`AudioClip.pcm`.

The packaging review added a Docker BuildKit regression test that materializes
the effective context into a scratch root filesystem. It proves that required
runtime assets remain and a synthetic future hardware note, a nested `.env`, a
nested artifact, bytecode, and a model weight remain excluded. The final
focused packaging run reported `6 passed`. Rebuilt-image checks also confirmed
the PortAudio SONAME files are real symlinks, with no `ldconfig` warning.

## Conclusion and limits

Python 3.14 is qualified for the existing repository tests and for actual
offline Pocket TTS and MediaPipe model initialization in the pinned Lyrical
container. This experiment did not qualify speaker playback, camera capture,
Pulse/ALSA host routing, ROS graph behavior, serial access, or physical motion.
The ALSA-only PortAudio build avoids requiring a fake audio server and is
consistent with the later explicit host audio overlay; that route still needs
its own Task 4 qualification. `PocketTtsWorker` loads lazily, so the future TTS
node must perform an explicit bounded model warmup before any hardware can be
armed.
