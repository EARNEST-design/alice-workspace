# Passive Alice Face Pilot

Date: 2026-08-31

Outcome: provisional thresholds declared; replacement capture pending

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

## Pre-Capture Status

The archived runs remain exploratory and inconclusive.

The procedural gap is now explicit: the original three runs were captured before a pre-capture confirmation of full-face framing and participant exclusion. That alone disqualifies them from Phase 1 acceptance evidence. They still remain useful as exploratory observations, and their analyzer outcome also stays `inconclusive` because no acceptance thresholds were predeclared.

That blocker is now cleared. Replacement capture can proceed under the frozen threshold set above. No robot motion was commanded.

## Confirmatory Rerun Requirements

- Predeclare acceptance thresholds before capture for minimum detection rate, maximum within-run standard deviation, maximum percentile range, maximum warm-up drift, and maximum repeat-to-repeat mean delta for the dominant categories.
- Keep the same stable by-id capture node unless new evidence shows a better mapping.
- Record measured distance and angle instead of leaving them unknown.
- Lock focus/exposure if possible, or at minimum record the exact auto/manual state before the run.
- Independently verify participant exclusion.
- Provide a repo-supported read-only capability probe such as `v4l2-ctl`, or record an equivalent reviewed capability listing before the rerun.
