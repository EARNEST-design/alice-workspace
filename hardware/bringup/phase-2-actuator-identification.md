# Proposed Phase 2 actuator-identification bring-up

Status: **not approved; non-executable repository template**  
Scope: one actuator (`mouth_open`, Maestro channel 6), Home → `+0.05` → Home → `-0.05` → Home.  
Do not use this document as approval. A hardware run requires a fresh operator review and a separately edited, uncommitted run configuration.

## Recorded identities and limits

- Controller: Pololu Mini Maestro 12, serial `00037376`.
- Recorded interfaces:
  - interface 00: `/dev/serial/by-id/usb-Pololu_Corporation_Pololu_Mini_Maestro_12-Channel_USB_Servo_Controller_00037376-if00`
  - interface 02: `/dev/serial/by-id/usb-Pololu_Corporation_Pololu_Mini_Maestro_12-Channel_USB_Servo_Controller_00037376-if02`
- The functional command-port role of interface 00 is **unresolved**. Confirm it without Set Target before approval.
- Reviewed manifest file SHA-256: `626a37d265bc7f2b055682cc1b1d287b1f0d659040d60ef4e5634ee675c05a8a`.
- Calibration SHA-256: `8ad9ad1f59dc70c47515c9a37b4531c5070c22a40ed66fb673d0e72a0d3dadb4`.
- `mouth_open`: channel 6; firmware speed `0`, acceleration `11`; Home `5059` quarter-microseconds; software range `4608..5440`.
- Initial normalized offset is only `±0.05`; Home tolerance is `12` quarter-microseconds. Larger offsets and any other actuator require a new procedure/configuration review.
- Command interval `1000 ms`; controller settle allowance `1000 ms`; visual settle allowance `1000 ms`; ten samples at `100 ms`; step timeout `10000 ms`; supervisor watchdog `5000 ms`; independent OS-monotonic watchdog `2500 ms`.

## Stop and power-removal rule

The master servo switch stays **OFF** throughout preparation and read-only checks. Before power is enabled, the operator must identify the switch or supply disconnect that removes servo power and keep a hand within immediate reach. On unexpected motion, binding, noise, heat, camera loss, controller error, software exception, watchdog trip, or operator uncertainty: remove servo power first, then stop the process. Do not rely on software Home as an emergency stop.

The independent watchdog is a daemon thread in the same process. It revokes permits and closes the serial adapter if runner progress stops or an exception unwinds. It cannot survive kernel failure, host power loss, process kill, or a wedged interpreter; closing serial also cannot guarantee that powered servos return Home. Operator power removal is therefore the authoritative stop.

## Preflight checklist (all fresh and typed)

With master servo power OFF:

1. Confirm Alice is mechanically supported and people, cables, clothing, tools, and camera are outside the motion envelope.
2. Inspect channel 10's previously jammed linkage even though this first run selects channel 6. Do not proceed if it binds or its condition is uncertain.
3. Establish supply/current limits from reviewed electrical evidence. This remains unresolved in the repository.
4. Confirm the emergency power-removal control works and remains reachable.
5. Confirm no ROS, Maestro Control Center, serial terminal, prior Alice process, or other program owns either Maestro interface.
6. Confirm, without Set Target, that interface 00 is the command interface for serial `00037376`. This remains unresolved in the repository.
7. Confirm Phase 1 camera acceptance from a fixed, locked setup. The recorded pilot currently **failed** its frozen stability thresholds, so this gate is unresolved.
8. Copy the hardware YAML outside version control; replace the `REQUIRED_...` run ID, approval ID, and enable token. Never commit the token.
9. Run the dry verifier. It prints the exact hashes and has no serial-open or Set Target path:

   ```bash
   uv run alice-hardware-preflight \
     --config /absolute/path/to/uncommitted-run.yaml \
     --manifest hardware/alice-face-v1.yaml
   ```

10. Review the exact file hashes. A second invocation with `--enable-hardware` still performs validation only; type the full hash-bound phrase when prompted. This is not execution approval.
11. Record a contemporaneous `HardwarePreflightAttestation`. Only the trusted application composition may call `run_hardware_identification`; it accepts no adapter, transport, supervisor, clock, factory, or dynamic import seam.

## Device-reading preflight (explicit, still no motion command)

Only after every non-device item passes may the trusted composition open the exact interface-00 path. It reads the Maestro error register and controller output positions; it does not issue Set Target. Require error register `0x0000` and channel 6 within `12` quarter-microseconds of Home. These readings describe controller command output, not measured mechanical position. If either check fails, close the adapter, keep/remove servo power, and stop.

## Approved-run sequence (future approval only)

After the user separately approves this exact procedure and exact hashes, enable servo power only at the stated operator checkpoint. Observe Alice continuously. The runner commands channel 6 only: Home, `+0.05`, Home, `-0.05`, Home. It waits for controller-output and camera settling before each sample and independently verifies each Home. Never continue after a fault.

Recovery to Home is permitted only while the supervisor knows the complete applied command state and communications remain healthy. If command state is ambiguous, a write/read fails, the adapter is poisoned, or the watchdog closes it, software must not guess or send recovery motion: revoke all permits, close serial, remove servo power, and require manual inspection plus a new preflight.

## Abort and completion evidence

Stop on any controller error; Home miss; visual variance breach; face/camera loss; timeout; unexpected movement/noise/heat; binding; competing process; hash/identity change; or operator request. Preserve only derived blendshape/actuator artifacts and manifests—no raw face imagery. A completed run must end with independently verified Home, error register zero, adapter closed, and servo power removed. Record faults even when the run aborts.
