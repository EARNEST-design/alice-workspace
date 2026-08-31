# Passive Alice Face Pilot

Date: 2026-08-31

Outcome: exploratory and inconclusive

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

## Preview Gate

Before any replacement capture, run:

```bash
uv run alice-camera preview /dev/v4l/by-id/usb-046d_HD_Webcam_C525_79C73260-video-index0 --model-path artifacts/passive-alice-face-pilot/model/face_landmarker.task --width 640 --height 480 --fps 10 --window-title "Alice Phase 1 Preview"
```

The operator must confirm all of the following before starting a new capture:

- OBS Studio is closed.
- The preview uses the C525 Alice-facing selector, not the C920 selector.
- Alice's full face is visible in frame.
- No participant is visible in frame.

Exit the preview with `q`.

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

These archived runs are exploratory and inconclusive.

The procedural gap is now explicit: the original three runs were captured before a pre-capture confirmation of full-face framing and participant exclusion. That alone disqualifies them from Phase 1 acceptance evidence. They still remain useful as exploratory observations, and their analyzer outcome also stays `inconclusive` because no acceptance thresholds were predeclared.

No robot motion was commanded. The remaining blocker is operator confirmation in the preview gate, not actuator behavior.

## Confirmatory Rerun Requirements

- Predeclare acceptance thresholds before capture for minimum detection rate, maximum within-run standard deviation, maximum percentile range, maximum warm-up drift, and maximum repeat-to-repeat mean delta for the dominant categories.
- Keep the same stable by-id capture node unless new evidence shows a better mapping.
- Record measured distance and angle instead of leaving them unknown.
- Lock focus/exposure if possible, or at minimum record the exact auto/manual state before the run.
- Independently verify participant exclusion.
- Provide a repo-supported read-only capability probe such as `v4l2-ctl`, or record an equivalent reviewed capability listing before the rerun.
