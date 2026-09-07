# Passive Alice Face Pilot

Date: 2026-08-31

Outcome: replacement evidence failed the frozen provisional thresholds

## Camera Inventory

- Alice-facing Phase 1 camera: `usb-046d_HD_Webcam_C525_79C73260-video-index0`
- C525 metadata sibling present: `usb-046d_HD_Webcam_C525_79C73260-video-index1`
- Future user-facing camera, not to be opened for Phase 1 capture: `usb-046d_HD_Pro_Webcam_C920_BF4BEEAF-video-index0`
- C920 metadata sibling present: `usb-046d_HD_Pro_Webcam_C920_BF4BEEAF-video-index1`
- Read-only inventory hub note:
  - C525 `ID_PATH=pci-0000:75:00.3-usb-0:3:1.2`
  - C920 `ID_PATH=pci-0000:75:00.4-usb-0:2.3:1.0`
- Read-only mode inventory confirmed both capture selectors support at least:
  - `640x480` at `30`, `10`, and `5` fps
  - `1280x720` at `30`, `10`, and `5` fps
  - `1920x1080` at `30`, `10`, and `5` fps
- This pilot and the preview gate use only the C525 selector `usb-046d_HD_Webcam_C525_79C73260-video-index0`.
- `retain_frames: false`; no raw frames or video were saved.

Known placement facts on 2026-08-31:

- The user reported the webcam points at Alice's face.
- Unknown: distance, angle, lighting, focus mode, exposure mode.
- Full-face framing was not confirmed before the original three runs.
- Participant exclusion was not independently verified before the original three runs.

## Preview Confirmation

- At `2026-08-31T10:42:22Z` (`2026-08-31T18:42:22+0800 HKT`), after running `alice-camera preview` on `/dev/v4l/by-id/usb-046d_HD_Webcam_C525_79C73260-video-index0`, the user confirmed:
  - Alice's full face was visible in frame.
  - No participant was visible in frame.
  - The preview overlay tracked Alice correctly.
- OBS Studio was closed before the confirmation preview and will remain closed for replacement capture.
- The preview window was closed after confirmation and before replacement capture.

## Preview Gate

The preview gate has been satisfied. For provenance, the command used was:

```bash
uv run alice-camera preview /dev/v4l/by-id/usb-046d_HD_Webcam_C525_79C73260-video-index0 --model-path artifacts/passive-alice-face-pilot/model/face_landmarker.task --width 640 --height 480 --fps 10 --window-title "Alice Phase 1 Preview"
```

The confirmed preview conditions were:

- OBS Studio was closed.
- The preview used the C525 Alice-facing selector, not the C920 selector.
- Alice's full face was visible in frame.
- No participant was visible in frame.
- The overlay tracked Alice correctly.
- The preview was exited with `q` and closed before replacement capture.

## Provisional Threshold Declaration

These thresholds are frozen before replacement capture and are derived only from the archived exploratory runs `passive-alice-face-warmup-20260831`, `passive-alice-face-repeat-1-20260831`, and `passive-alice-face-repeat-2-20260831`.

Threshold formula:

- Dominant categories are the categories whose mean score was at least `0.30` in all three archived exploratory runs.
- `minimum_detection_rate` is fixed at `0.995`, which allows up to six invalid observations in a 1200-frame run while still requiring near-perfect face detection.
- For each dominant category, `maximum_standard_deviation`, `maximum_percentile_range`, and `maximum_warmup_drift` equal the archived worst-case value rounded up to `0.001`.
- Cross-run repeat agreement is evaluated separately from the per-run analyzer: for each dominant category, the provisional `maximum_abs_mean_delta` between fresh Repeat 1 and Repeat 2 equals the archived exploratory `|repeat-1 mean - repeat-2 mean|`, rounded up to `0.001`.

Per-run analyzer thresholds stored in the config:

| Category | Max Std Dev | Max 5-95 Range | Max Warm-up Drift |
| --- | ---: | ---: | ---: |
| `browOuterUpLeft` | `0.052` | `0.047` | `0.002` |
| `browInnerUp` | `0.036` | `0.077` | `0.006` |
| `browOuterUpRight` | `0.041` | `0.078` | `0.008` |
| `eyeLookDownLeft` | `0.046` | `0.131` | `0.064` |
| `eyeLookDownRight` | `0.047` | `0.147` | `0.032` |
| `mouthSmileLeft` | `0.058` | `0.151` | `0.024` |
| `mouthSmileRight` | `0.038` | `0.126` | `0.024` |
| `eyeWideRight` | `0.059` | `0.167` | `0.070` |
| `mouthPucker` | `0.044` | `0.111` | `0.015` |

Global per-run threshold:

- `minimum_detection_rate >= 0.995`

Cross-run provisional repeat thresholds for the fresh acceptance repeats:

These values are now represented by the typed
`repeatability_thresholds.maximum_mean_delta` mapping in the tracked pilot
configuration. The table and the historical calculations below remain the
original experimental evidence; they have not been recomputed or relabeled.

| Category | Max Abs Mean Delta (`repeat-1` vs `repeat-2`) |
| --- | ---: |
| `browOuterUpLeft` | `0.017` |
| `browInnerUp` | `0.024` |
| `browOuterUpRight` | `0.010` |
| `eyeLookDownLeft` | `0.001` |
| `eyeLookDownRight` | `0.004` |
| `mouthSmileLeft` | `0.046` |
| `mouthSmileRight` | `0.052` |
| `eyeWideRight` | `0.001` |
| `mouthPucker` | `0.012` |

## Replacement Acceptance Runs

All three replacement runs were captured after the confirmed preview using the exact C525 selector, with `retain_frames: false`, OBS closed, and manifests pinned to the frozen threshold revision `214ddd9`.

| Kind | Run ID | Start (UTC) | Duration (s) | Detection Rate | Analyzer Outcome |
| --- | --- | --- | ---: | ---: | --- |
| Warm-up | `passive-alice-face-confirmed-warmup-20260831T104222Z` | `2026-08-31T10:46:11.162739Z` | 120.98 | 1.0000 | `pass` |
| Repeat 1 | `passive-alice-face-confirmed-repeat-1-20260831T104222Z` | `2026-08-31T10:48:20.458832Z` | 120.98 | 1.0000 | `fail` |
| Repeat 2 | `passive-alice-face-confirmed-repeat-2-20260831T104222Z` | `2026-08-31T10:50:29.634959Z` | 120.98 | 1.0000 | `fail` |

Each replacement run produced 1200 valid observations, zero invalid observations, and only the provenance-safe files `manifest.json`, `observations.jsonl`, `stability-metrics.json`, and `phase-1-conclusion.md`. No raw images or video were saved.

That sentence describes the 2026-08-31 legacy artifact layout. The current
analyzer does not rewrite those ignored artifacts automatically. A deliberate
offline rerun publishes new immutable analysis generations while leaving each
capture manifest byte-for-byte unchanged.

## Replacement Run Findings

- The warm-up run passed every frozen per-run threshold.
- Repeat 1 failed one threshold:
  - `browOuterUpLeft.warmup_drift`: observed `0.002551` vs threshold `0.002`
- Repeat 2 failed three thresholds:
  - `browOuterUpLeft.warmup_drift`: observed `0.006622` vs threshold `0.002`
  - `browOuterUpRight.warmup_drift`: observed `0.018544` vs threshold `0.008`
  - `mouthSmileRight.warmup_drift`: observed `0.025463` vs threshold `0.024`
- All three replacement runs met the global `minimum_detection_rate >= 0.995` threshold with `1.0000`.
- The failures are therefore stability failures, not face-detection failures.

## Repeat-To-Repeat Comparison

The two fresh acceptance repeats were also compared against the predeclared cross-run `maximum_abs_mean_delta` limits:

| Category | Repeat 1 Mean | Repeat 2 Mean | Abs Delta | Threshold | Status |
| --- | ---: | ---: | ---: | ---: | --- |
| `browOuterUpLeft` | `0.964202` | `0.951952` | `0.012251` | `0.017` | `pass` |
| `browInnerUp` | `0.915934` | `0.909489` | `0.006445` | `0.024` | `pass` |
| `browOuterUpRight` | `0.916115` | `0.894727` | `0.021388` | `0.010` | `fail` |
| `eyeLookDownLeft` | `0.527691` | `0.497882` | `0.029809` | `0.001` | `fail` |
| `eyeLookDownRight` | `0.488912` | `0.459697` | `0.029215` | `0.004` | `fail` |
| `mouthSmileLeft` | `0.128366` | `0.148851` | `0.020484` | `0.046` | `pass` |
| `mouthSmileRight` | `0.145299` | `0.162976` | `0.017677` | `0.052` | `pass` |
| `eyeWideRight` | `0.399650` | `0.436298` | `0.036648` | `0.001` | `fail` |
| `mouthPucker` | `0.091812` | `0.106644` | `0.014832` | `0.012` | `fail` |

Five of the nine dominant categories exceeded their predeclared repeat-to-repeat mean-delta limits.

## Archived Exploratory Runs

| Kind | Run ID | Start (UTC) | Duration (s) | Detection Rate |
| --- | --- | --- | ---: | ---: |
| Warm-up | `passive-alice-face-warmup-20260831` | `2026-08-31T09:20:21.628793Z` | 141.48 | 1.0000 |
| Repeat 1 | `passive-alice-face-repeat-1-20260831` | `2026-08-31T09:26:28.599948Z` | 141.50 | 1.0000 |
| Repeat 2 | `passive-alice-face-repeat-2-20260831` | `2026-08-31T09:28:59.959219Z` | 140.36 | 1.0000 |

All three runs produced 1200 valid observations and zero invalid observations, but they remain exploratory only because full-face framing and participant exclusion were not confirmed before capture.

## Observations

Persistent high-baseline categories across all three runs included `browOuterUpLeft`, `browInnerUp`, `browOuterUpRight`, `eyeLookDownLeft`, `eyeLookDownRight`, `mouthSmileLeft`, `mouthSmileRight`, `mouthPucker`, and `eyeWideRight`. These scores stayed well above zero in every run, but several of them moved enough between repeats to show setup sensitivity rather than a settled acceptance envelope.

Near-zero floor categories across all three runs included `_neutral`, `cheekSquintLeft`, `cheekSquintRight`, `noseSneerRight`, `mouthFrownLeft`, `mouthFrownRight`, and `mouthClose`. `jawOpen`, `jawLeft`, and `jawRight` also stayed close to zero throughout the stationary pilot. In this passive setup they behaved more like detector floor/noise than useful signal.

Repeat 2 was visibly less stable than Repeat 1 in several dominant categories:

- `browOuterUpLeft` standard deviation increased from `0.0078` to `0.0513`.
- `browOuterUpRight` standard deviation increased from `0.0109` to `0.0401`.
- `eyeLookDownLeft` standard deviation increased from `0.0227` to `0.0456`, and warm-up drift increased from `0.0051` to `0.0635`.
- `mouthSmileLeft` standard deviation increased from `0.0323` to `0.0577`.
- `eyeWideRight` warm-up drift increased from `0.0112` to `0.0697`.

The largest repeat-to-repeat mean shifts were:

- `mouthSmileRight`: `0.4175` to `0.3661` (`abs delta 0.0514`)
- `mouthSmileLeft`: `0.4729` to `0.4278` (`abs delta 0.0451`)
- `browInnerUp`: `0.8946` to `0.8714` (`abs delta 0.0232`)

## Conclusion

The archived runs remain exploratory and inconclusive, but the replacement evidence is not inconclusive.

Phase 1 failed the frozen provisional thresholds:

- Warm-up passed, but both fresh acceptance repeats failed the per-run analyzer thresholds.
- Five of nine dominant categories also failed the predeclared repeat-to-repeat mean-delta limits.
- Detection remained perfect in all three replacement runs, so the failure is stability-related rather than detector-availability-related.

This is still a provisional Phase 1 judgment because the thresholds themselves were derived from earlier exploratory runs rather than a separately approved acceptance study. Even so, the replacement evidence shows that the confirmed-preview setup did not stay within the frozen provisional stability envelope.

No robot motion was commanded. Unknowns remain unchanged: distance, angle, lighting state, focus mode, and exposure mode were not measured or locked during this Phase 1 work.

## Stabilized-control rerun — 2026-09-01 HKT

A later rerun used the same C525 stable selector with camera controls read back
and locked before capture: continuous autofocus `0`, absolute focus `60`,
manual exposure mode `1`, absolute exposure `249`, dynamic framerate `0`,
automatic white balance `0`, white-balance temperature `3420`, gain `25`, and
50 Hz power-line compensation. OpenCV independently reported `640x480` at
10 fps, focus `60`, and exposure `249` in every capture manifest. The operator
accepted sharpness, exposure, full-face framing, overlay tracking, and
participant exclusion in the live preview. The frozen setup revision was
`f857c115047a9862a00afbbca5870c1d9593b953`. Raw frames remained disabled.

| Kind | Run ID | Valid observations | Detection rate | Outcome |
| --- | --- | ---: | ---: | --- |
| Warm-up | `passive-alice-face-stabilized-warmup-20260901` | 1200/1200 | 1.0000 | pass |
| Repeat 1 | `passive-alice-face-stabilized-repeat-1-20260901` | 400/1200 | 0.3333 | fail |
| Repeat 2 | `passive-alice-face-stabilized-repeat-2-20260901` | 0/1200 | 0.0000 | fail |

Repeat 1 produced valid detections through `2026-08-31T19:39:23.951195Z` and
then continuously reported `no face detected` from
`2026-08-31T19:39:24.051206Z` onward. Repeat 2 remained `no_face` for its full
duration. The camera continued delivering frames and all negotiated and V4L2
control values remained unchanged. The valid portion of Repeat 1 passed every
per-category variance and drift threshold; its only failed check was detection
rate. Repeat 2 had no valid category set, so the immutable repeatability
publisher correctly rejected a category-schema comparison rather than
inventing comparable metrics.

This evidence localizes the current failure to loss of detector-visible face
content after the first 40 seconds of Repeat 1. It does not establish whether
the physical cause was framing, occlusion, illumination, or another scene
change because no raw imagery was retained. Phase 1 therefore remains failed
until the reopened live overlay is visually checked, the cause is corrected,
and fresh locked repeats pass.

## C920 comparison — 2026-09-02 HKT

After correcting a visibly flickering light, the newer C920 was repositioned
toward Alice and compared at `1280x720`, 10 fps. Its locked V4L2 controls were
continuous autofocus `0`, absolute focus `50`, manual exposure mode `1`,
absolute exposure `100`, dynamic framerate `0`, automatic white balance `0`,
white-balance temperature `3277`, gain `0`, and 50 Hz power-line compensation.
The operator reported a visibly sharper image but similar landmark-dot jitter.

An initial warm-up requested 30 fps but negotiated 10 fps and is excluded from
the exact-configuration comparison. The corrected configuration and all exact
runs used commit `bf1800283b4834208e9a4bf407b88b3d13a32852`; their manifests
reported `1280x720`, 10 fps, focus `50`, and exposure `100`.

| Kind | Valid observations | Per-run outcome | Failed per-run checks |
| --- | ---: | --- | --- |
| Warm-up | 1200/1200 | fail | `browInnerUp.warmup_drift`: 0.006238 > 0.006 |
| Repeat 1 | 1200/1200 | fail | `browInnerUp.warmup_drift`: 0.009843 > 0.006; `browOuterUpRight.warmup_drift`: 0.008808 > 0.008 |
| Repeat 2 | 1200/1200 | pass | none |

The same frozen cross-run thresholds were used for a direct comparison:

| Blendshape | C525 delta | C920 delta | Threshold | Result |
| --- | ---: | ---: | ---: | --- |
| `eyeLookDownLeft` | 0.009788 | 0.003368 | 0.001 | C920 improved, both fail |
| `eyeWideRight` | 0.001779 | 0.002889 | 0.001 | C525 improved, both fail |
| `mouthPucker` | 0.001263 | 0.001365 | 0.012 | both pass |
| `mouthSmileLeft` | 0.005874 | 0.000580 | 0.046 | C920 improved, both pass |
| `mouthSmileRight` | 0.004610 | 0.000534 | 0.052 | C920 improved, both pass |

The C920 therefore improves image sharpness and several mouth scores, but does
not make the frozen eye dimensions repeatable and introduces additional brow
drift failures. A camera replacement alone is not sufficient. The corrected
C525 setup remains the stronger general baseline because all three of its
per-run stability checks passed. Eye-actuator identification must preserve
uncertainty and repeated Home baselines rather than treating visually stable
landmark dots as exact measurements.

## Follow-Up Requirements

- Keep the same stable by-id C525 capture selector unless new reviewed evidence shows a better mapping.
- Measure and record distance and angle instead of leaving them unknown.
- Record the exact lighting state and lock focus/exposure if possible, or at minimum record the exact auto/manual state before the run.
- Preserve the same preview confirmation procedure for participant exclusion and full-face framing before any future rerun.
- If a future rerun is intended as acceptance evidence, review and approve the threshold-setting method separately instead of deriving it from the same exploratory family of runs.
- Add a non-biometric scene-health signal (for example aggregate luminance and
  a detector-visible-face transition event) so future no-face failures can be
  diagnosed without retaining images.
