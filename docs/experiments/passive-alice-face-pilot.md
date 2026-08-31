# Passive Alice Face Pilot

Date: 2026-08-31

Outcome: inconclusive

## Camera Selection

- Read-only discovery found two stable by-id siblings for the Logitech C525: `usb-046d_HD_Webcam_C525_79C73260-video-index0 -> /dev/video0` and `usb-046d_HD_Webcam_C525_79C73260-video-index1 -> /dev/video1`.
- `udevadm` reported `ID_V4L_CAPABILITIES=:capture:` for `/dev/video0` only, so this pilot used `/dev/v4l/by-id/usb-046d_HD_Webcam_C525_79C73260-video-index0`.
- `alice-camera list` could not report supported modes on this host because `v4l2-ctl` is unavailable.
- `retain_frames: false`; no raw frames or video were saved.
- The user later confirmed OBS Studio was closed before the experimental repeat runs.

Known placement facts on 2026-08-31:

- The user reported the webcam points at Alice's face.
- Unknown: distance, angle, lighting, focus mode, exposure mode.
- Participant exclusion was not independently verified.

## Runs

| Kind | Run ID | Start (UTC) | Duration (s) | Detection Rate |
| --- | --- | --- | ---: | ---: |
| Warm-up | `passive-alice-face-warmup-20260831` | `2026-08-31T09:20:21.628793Z` | 141.48 | 1.0000 |
| Repeat 1 | `passive-alice-face-repeat-1-20260831` | `2026-08-31T09:26:28.599948Z` | 141.50 | 1.0000 |
| Repeat 2 | `passive-alice-face-repeat-2-20260831` | `2026-08-31T09:28:59.959219Z` | 140.36 | 1.0000 |

All three runs produced 1200 valid observations and zero invalid observations.

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

This pilot is inconclusive.

The immediate reason is procedural: no acceptance thresholds were predeclared, so the repo's analyzer correctly marked every run as `inconclusive`. The underlying measurements also do not yet show a clearly repeatable stationary envelope, because Repeat 2 widened variance and warm-up drift in several of the highest-baseline categories even though detection never failed.

No robot motion was commanded. The limiting factors were camera/setup uncertainty, not actuator behavior.

## Confirmatory Rerun Requirements

- Predeclare acceptance thresholds before capture for minimum detection rate, maximum within-run standard deviation, maximum percentile range, maximum warm-up drift, and maximum repeat-to-repeat mean delta for the dominant categories.
- Keep the same stable by-id capture node unless new evidence shows a better mapping.
- Record measured distance and angle instead of leaving them unknown.
- Lock focus/exposure if possible, or at minimum record the exact auto/manual state before the run.
- Independently verify participant exclusion.
- Provide a repo-supported read-only capability probe such as `v4l2-ctl`, or record an equivalent reviewed capability listing before the rerun.
