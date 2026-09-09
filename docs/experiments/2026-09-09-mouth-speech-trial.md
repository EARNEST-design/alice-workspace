# Mouth-only speech trial preparation

Goal: replay operator-selected Azelma speech on the calibrated jaw, with all
11 semantic servos mapped for later expression trials. Engineering review of
the new executor and procedure passed; the initial qualification preceded physical execution. Results of the subsequent
operator-requested hardware and camera trials are recorded below.

Configuration: `config/speech/jaw-trial-v1.json`.
Procedure: `hardware/bringup/mouth-speech-trial-v1.md`.
Source: `artifacts/speech/azelma-sync-reviewed/neutral-priors-only/`.
The original speech manifest retains model/voice/seed and source provenance.
WAV SHA256: `c7c196daec91fa457c431fedadb56290aeb0f475669f20723c7b0fc732e808de`.

Host dependency `libportaudio2` was installed. Playback used the existing default
ALSA output. No microphone/camera was opened.

## Runs and evidence

- `artifacts/speech/jaw-mock-audio-1/`: aborted during entry before audio. The
  supervisor caught a 4.0049/s² requested acceleration caused by authorization
  clock drift. A regression injects 2 ms drift; the planner now intersects valid
  bounds across that interval without relaxing the supervisor.
- `artifacts/speech/jaw-mock-audio-2/`: real audio/mock jaw completed after that
  fix. Subsequent review added explicit cleanup evidence and endpoint handling.
- `artifacts/speech/jaw-mock-audio-3/`: real audio/mock jaw completed in 9.694 s.
  233 commands, 153 during speech, 318 audio frames, target range -1 to +0.300418,
  final exact Home 0, ACK intervals 40.249–42.561 ms. The directory contains
  `manifest.json`, retained inputs/code/config/procedure, `commands.json` and
  `playback.json` with checksums. The difference from the raw aperture peaks is
  intentional rate/acceleration limiting, not measured servo tracking.

Independent review found ambiguous fuser failures and endpoint convergence with
25 ms delayed ACKs. Both are fixed and covered by regressions. Full expression
mapping and final jaw-only selection are tested. Fault, EOF, source-stall,
non-Home/disabled output, and close-error tests verify fail-closed behavior and
preservation of the physical power-off prompt.

Conclusion: the software path is ready for the short attended physical trial.
Physical tracking, perceived synchronization and electrical margin remain
unmeasured. The procedure retains the full assembly clearance check and explicit
operator acceptance of the short trial on the recorded supply. Do not interpret
software or mock results as physical acceptance.

Final verification: `uv run pytest -q` — 752 passed in 27.65 s, with two existing
fork-in-multithreaded-process deprecation warnings; `uv run ruff check src tests`
passed; `uv run mypy src` passed for 72 files; `git diff --check` passed.
Read-only sysfs and fuser checks found both expected Maestro interfaces present
with matching identity and no owners. These checks did not open serial.

## Operator-directed hardware completion and camera check

The operator superseded repeated readiness/RUN/OFF steps, declared the robot
powered and attended at the master switch, then approved full calibrated jaw
range and robot-camera/blendshape verification. Updated both workspace copies
of AGENTS.md and the speech procedure accordingly. Automatic controller checks
and normal trajectory bounds remain.

- Hardware trial 2: no target write; discovered disabled jaw output 0.
- Trial 3: enabled channel 6 at Home; transient zero observation exposed an
  overly strict ControllerOutputSample schema. Fixed with a reproduction test.
- Trial 4: 75 acknowledged jaw commands; six-second entry timeout at -0.9902;
  audio did not start. Measured approximately 40 ms controller update latency.
- Trial 5: first complete audio/controller run, 155 commands, 82 during speech,
  Home confirmed. Operator did not see speech mouth movement.
- Trial 6: 100 ms dispatch interval, 71 commands; still only negative speech
  excursion (-1 to -0.2174). Operator correctly identified closure-only behavior.

The existing mouth mimicry script confirms channel 6 and direct full-range
mapping. An explicit full-range silent diagnostic then commanded -1 to +1 and
returned Home. C525 camera/MediaPipe observed 7.4–38.3 px lip gap with valid face
detection on 158/158 frames. The stored closed/open images visibly confirm the
movement. Data: `artifacts/speech/jaw-full-range-camera-1/`.

Recalibrated the waveform envelope using the existing `full_open_rms` parameter
(0.06, formerly 0.15), retaining Azelma's original PCM and provenance. Used
`sync-hardware-v1.json` and `jaw-full-range-v1.json`. In
`artifacts/speech/azelma-full-range-camera-1/`, the actual speech targets crossed
neutral (-1 to +0.5764), and lip gap measured 7.6–32.7 px during speech. All
129 camera frames detected Alice; audio completed, 74 commands were acknowledged,
and exact controller Home was restored. Total duration 11.24 s.

Robot geometry makes mouthUpperUpLeft/Right useful movement indicators;
MediaPipe's nominal jawOpen remained nearly zero. This agrees with the previous
mimicry characterization. The images and landmark-gap change establish visible
jaw movement; controller acknowledgments alone did not. Exact physical lip-sync
lag is still unmeasured. Recorded the requested incremental LLM/TTS/audio-streaming
architecture as ADR 0009; this trial still used retained audio.

## Faster servo response and remaining delay

`azelma-streamed-camera-1` removed per-target settling waits: 166 sent targets,
153 during speech, camera lip range 7.51–38.07 px, 88/88 valid detections,
mean serial round trip 1.017 ms, completed audio and Home in 7.106 s. The user
reported better movement with residual lag and requested servo speed tuning.
Read-only device settings confirmed speed 0 (unlimited), acceleration 11.

`azelma-fast-servo-camera-1` used runtime jaw speed/acceleration 0/0, the same
Azelma PCM/envelope and `jaw-speech-fast-v1.json`. It completed in 6.848 s with
166 commands, 154 during speech, all immediate PWM observations matching targets
(mean/max target error both 0, versus previous mean 188.9/max 581 quarter-us).
Mean serial round trip was 0.840 ms (0.617–3.177 ms). Camera detected Alice on
85/85 frames, with 7.50–36.68 px lip gap during speech. Open/closed images were
visually inspected. Audio completed, Home was confirmed, and runtime 0/11 was
restored. EEPROM was unchanged. User feedback: “looking good i feel there's
still some delay”.

Camera read-time cross-correlation suggests apparent envelope-to-image delay
fell from about 380 to 190 ms, and command-to-image delay from 290 to 120 ms.
These figures include unknown camera buffering/exposure timing and are not
calibrated mechanical/acoustic latency measurements. A modest 100 ms mouth lead
is therefore being tested rather than treating the correlation peak as exact.
The next profile keeps all motion caps and advances only aperture lookup;
current audio, expression, affect and speech ownership clocks remain intact.

Review caught a fault-cleanup race introduced by runtime restoration in close.
It is fixed: watchdog/fault close sends no serial commands; normal completion
restores the profile on the serial owner after Home confirmation. A threaded
blocked-read regression reproduces and prevents the interleaving. Send-time
kinematics also now exclude post-write readback delay; cleanup preserves the
primary fault. Independent review found no remaining actionable issues in these
fixes or the aperture-only lookahead.


## 100 ms mouth lead trial, 2026-09-09

`artifacts/speech/azelma-lead100-camera-1/` used `jaw-speech-lead-v1.json`,
`--stream-targets --fast-jaw-response` and the same retained Azelma PCM. It
completed in 6.878 s: 166 sent targets, 153 during speech, full -1…+1 command
range, all immediate PWM readbacks equal to their targets, and 0.860 ms mean
serial round trip (0.593–2.557 ms). Camera detections were valid on 85/85 frames;
speech lip gap spanned 7.27–37.14 px. Audio and Home completed, and the serial
owner restored runtime speed/acceleration 0/11 before closing. A new read-only
settings export exactly matched the pre-trial export; EEPROM remained unchanged.

Apparent envelope-to-image cross-correlation peaked at 80 ms (r=0.931), down
from 190 ms without lead. Command-to-image remained 120 ms (r=0.981), consistent
with advancing commands rather than changing the mechanical/camera response.
These estimates include unknown camera latency and are not precise acoustic
lip-sync measurements. The 100 ms lead is retained as an explicit tested profile;
normal defaults remain zero. The operator confirmed the final 100 ms lead trial: “Looks aligned”.
This is subjective acceptance for the tested clip and attended setup.

The metrics include analysis-script/input hashes; the trial manifest includes
source, configuration, procedure and input hashes. Robot-only camera imagery
remains in ignored local artifacts. No human camera or microphone was opened.

Final checks after the timing and watchdog fixes: `uv run pytest -q` passed
799 tests in 29.56 s with two existing fork deprecation warnings;
`uv run ruff check src tests` and `uv run mypy src/alice` passed (73 files).
Independent review found no actionable issues in the send-clock, fault-close,
normal restoration or aperture-lookahead changes.
