# Operator-verified expression presets

Controller serial: `00037376`. Values are quarter-microseconds and match the firmware-stored limits exported on 2026-08-28.

These are calibration evidence, not yet a production motion API. Production use still requires a watchdog, explicit schema/version identity, validated transitions, and guarded hardware enablement.

## Mouth expressions

| Pose | Channel 6: chin/mouth | Channel 9: left corner | Channel 11: right corner |
|---|---:|---:|---:|
| Home | 5059 | 6499 | 5524 |
| Smile with open mouth | 5440 | 6912 | 5120 |
| Frown with closed mouth | 4608 | 5120 | 6912 |

Physical polarity:

- Channel 6: decreasing closes/chin up; increasing opens/chin down.
- Channel 9: decreasing lowers/frowns; increasing raises/smiles.
- Channel 11: decreasing raises/smiles; increasing lowers/frowns.

The final validation used temporary speed `5`, acceleration `2`, waited for all reported positions within 12 quarter-microseconds, held each expression for five seconds, returned Home between poses, then restored firmware-exported runtime speed/acceleration. The operator accepted both expressions at 100% of their firmware ranges. No persistent controller settings were changed.

## Eyes

- Channel 8 (right eye) and channel 10 (left eye) share horizontal polarity: decreasing target looks right; increasing target looks left.
- Channel 10 was mechanically jammed during mapping and was manually freed. Inspect its linkage before unattended operation.
- Channels 3 (lower eyelids) and 4 (upper eyelids) close the eyes together. A coordinated gaze/blink experiment remains to be designed.
