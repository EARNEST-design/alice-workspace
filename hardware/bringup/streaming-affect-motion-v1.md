# Streaming affect-motion readiness v1

This gate produces derived JSON evidence only. It validates immutable hardware
manifest and model-package snapshots, checks stable Linux device identities from
by-id links and sysfs metadata, and runs deterministic 60-second software replay
through the production composer and controller-response model. It does not open
serial or camera devices, access frames, construct a hardware adapter, encode or
send Set Target, or move Alice.

## Preconditions

- Set the servo master switch **OFF** and keep it off for this procedure.
- Use the reviewed controller Command Port path ending in serial `00037376-if00`.
- Use the Alice-facing C525 capture identity ending in
  `C525_79C73260-video-index0`.
- Use the reviewed device-identity record
  `config/experiments/streaming-motion-readiness-v1.yaml`. It pins the exact
  Pololu command-port and C525 kernel/USB identities; do not substitute a
  copied record or use the CLI device arguments as an identity override.
- Install the repository environment with its software-model dependencies:
  `uv sync --extra ml`.
- Select one immutable `motion-model-package/v3` directory. Do not point the
  command at a package being trained, updated, or copied.

## Read-only command

From the repository root, replace only the model-package path with the reviewed
package to evaluate:

```bash
mkdir -p artifacts/readiness
uv run alice-motion-readiness \
  --hardware-manifest hardware/alice-face-v1.yaml \
  --device-config config/experiments/streaming-motion-readiness-v1.yaml \
  --model-package artifacts/models/streaming-affect-motion-v1 \
  --camera-device /dev/v4l/by-id/usb-046d_HD_Webcam_C525_79C73260-video-index0 \
  --json-output artifacts/readiness/streaming-affect-motion-v1.json
```

Exit `0` means every identity check and deterministic replay passed. Exit `2`
means fail closed. The compact report records per-check pass/fail reasons, exact
hardware/model/calibration/controller identities, manifest hashes, the virtual
duration, seeds, affect-vector count, prefix count, and a replay digest. It
contains no images, frames, participant data, raw controller traffic, or actuator
targets.

Archive the JSON beside the reviewed package and record its SHA-256 in the run
decision. A passing readiness report is software evidence only; it is not an
actuation permit and does not resolve the manifest's physical preflight items.

## Hardware execution boundary

Stop after this command. Do not infer permission to enable servo power or run
`alice-hardware-run`. Any later hardware command requires the separate reviewed
bring-up procedure, fresh operator attestations, current electrical and emergency
power-removal evidence, explicit user approval, and the guarded hardware CLI.
