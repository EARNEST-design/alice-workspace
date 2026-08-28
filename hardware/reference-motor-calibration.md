# Reference motor calibration (inactive)

These values were transcribed from the two reference repositories on 2026-08-28. The user identified `ros2_maestro` as the latest software record, with the first two motors intentionally unused. A read-only export from controller serial `00037376` now confirms all of its Min/Home/Max values except channel 2 Max: firmware stores `8832`, while commit `e15bccc6bf78e77a0872fd3eed807d6572a56874` records `8000`. The controller export is preserved in `hardware/maestro-00037376-settings-2026-08-28.txt`.

Units are Pololu Maestro quarter-microseconds (`qus`); microseconds equal `qus / 4`.

## `alice-feedback` (8 active channels)

| Array index | Channel | Min qus | Software neutral/home qus | Max qus |
|---:|---:|---:|---:|---:|
| 0 | 3 | 3136 | 5626 | 6066 |
| 1 | 4 | 4928 | 6173 | 7232 |
| 2 | 5 | 6016 | 6208 | 7232 |
| 3 | 6 | 4992 | 5059 | 5312 |
| 4 | 8 | 5632 | 5984 | 6336 |
| 5 | 9 | 5120 | 6499 | 6912 |
| 6 | 10 | 5824 | 5998 | 6272 |
| 7 | 11 | 5120 | 5524 | 6912 |

Source: `config/motor_ranges.yaml`. Message ordering is the sorted list of active channels, not a named facial schema.

## `ros2_maestro` (11 active channels)

| Array index | Channel | Min qus | Software neutral/home qus | Max qus |
|---:|---:|---:|---:|---:|
| 0 | 0 | 4928 | 6480 | 7808 |
| 1 | 1 | 3712 | 6007 | 8000 |
| 2 | 2 | 3712 | 7440 | 8000 (firmware: 8832) |
| 3 | 3 | 2880 | 5626 | 6400 |
| 4 | 4 | 3840 | 6173 | 7232 |
| 5 | 5 | 4032 | 5918 | 6592 |
| 6 | 6 | 4608 | 5059 | 5440 |
| 7 | 8 | 5632 | 6000 | 6144 |
| 8 | 9 | 5120 | 6499 | 6912 |
| 9 | 10 | 5696 | 6000 | 6400 |
| 10 | 11 | 5120 | 5524 | 6912 |

Source: `config/motor_ranges.yaml`; channel 7 is absent.

The repository field `neutral_position` matches the controller's startup/error `home` value on every listed channel. It is distinct from the Maestro's separate 8-bit-command `neutral` parameter.

## Compatibility findings

- `alice-feedback` currently produces 8 values; `ros2_maestro` expects 11.
- The ROS topic is `/face_motors` using `std_msgs/msg/Float32MultiArray`, normalized to `[-1, 1]`, ordered by sorted channel number.
- A short ROS input is silently padded with neutral values by `ros2_maestro`, potentially moving channels 0–2.
- Channels 0 and 1 are intentionally unused at the application/mechanical level, but firmware still configures them as `Servo` with `homemode="Goto"`; they are not electrically disabled by controller configuration.
- Channel 7 is absent from the ROS map and has `homemode="Off"`, but its firmware mode is `ServoMultiplied`.
- Firmware channel 2 Max (`8832`, 2208 us) differs from the current repository (`8000`, 2000 us); software commands are therefore more conservative than the controller clamp on that channel.
- Shared channels 3, 4, 5, 6, 8, and 10 have materially different limits between repositories; only 9 and 11 match exactly.
- Never run direct serial actuation from `alice-feedback` concurrently with the ROS actuator node.
- Neither repository provides semantic motor names, calibration version identity in messages, watchdog/deadman handling, or a feedback/status channel.
- Both use Maestro compact serial protocol. Set Target is `0x84 channel low7 high7`; the ROS driver can also group contiguous targets via `0x9F`.

## Controller-resident settings

The Maestro stores channel mode, Min, Max, 8-bit neutral/range, speed, acceleration, and startup/error behavior on the device. Firmware clamps servo output positions to configured Min/Max limits. The settings were exported over native USB using Pololu's `UscCmd --getconf`; no actuator or script command was issued. Channel display names are computer-local metadata rather than controller-resident settings.

## Required resolution

Create one versioned, semantically named hardware manifest after tracing the physical wiring. The actuation message must carry schema/calibration identity, timestamp, validity, and explicit channel identity; reject dimensional mismatches rather than truncating or padding. Add a watchdog, finite/range validation, slew/acceleration bounds, fault reporting, and deterministic neutral/disable behavior.
