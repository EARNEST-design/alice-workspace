# XIAO USB bridge and ROS hardware integration

Status: proposed architecture for review. The operator selected completing the
XIAO/ROS path before movement. No movement-capable bridge or ROS hardware
admission change has been implemented. The current firmware is read-only.

## Intended result

Run a bounded, calibrated facial movement from ROS through the XIAO's USB
connector, the translated UART and Maestro 00037376. Preserve default simulation,
controller checks, cancellation, source-age limits and durable evidence. Start
physical qualification with the jaw before admitting the existing six-channel
speech/face scope. ReSpeaker audio remains disconnected during this work;
simultaneous audio, Wi-Fi transport and body Dynamixels require separate work.

## Evidence and current blockers

The existing eight-node ROS simulation passed on 2026-09-16. Existing production
ROS hardware admission deliberately rejects because complete host controller
ownership has not been qualified. Changing a serial path cannot satisfy that
requirement. The installed XIAO probe implements no Set Target command.

The new CRC diagnostic still found corruption at 115200, 38400 and 9600 baud.
At 38400, `a1 1b` reached Maestro RX as `e1 1b`, and the controller set CRC error
bit 3. Other trials received extra `ff` bytes. Wiring qualification is mandatory
before movement. The operator reports 3.3/5 V supplies, common grounds and a
breadboard 5 V distribution rail; signal lengths and loaded VIN are unqualified.

## Approaches

1. **Recommended: a host USB owner plus a small XIAO bridge.** A narrowly scoped
   host process owns device access and exposes a local Unix socket to ROS. ROS
   containers retain their current isolation. The XIAO validates bounded commands
   and generates CRC-protected Maestro packets. This makes host ownership and
   firmware behavior independently testable and keeps the MCU transport small.
2. **micro-ROS on the XIAO.** Add the micro-ROS client and host agent, then qualify
   MCU lifecycle, memory, transport and the same Maestro safety interface. It
   still needs reliable UART wiring and does not itself solve controller
   ownership, calibration or Maestro error behavior. It adds work without a
   demonstrated benefit for the first USB bench trial.

## Recommended architecture

```mermaid
flowchart LR
  R[ROS motion participant] -->|bounded request and run identity| H[Host USB owner]
  H -->|framed USB protocol| X[XIAO ESP32S3]
  X -->|CRC commands through translator| M[Maestro 12]
  M --> S[Selected face servos]
  M -->|position and error replies| X
  X -->|matching response or fault| H
  H -->|receipt and health| R
```

### Device access and identity

- The host process is the sole device owner. It must establish complete host
  FD visibility, verify exact USB identities and resolve current character/raw
  USB devices. Permission failures and ambiguous ownership reject admission.
  Empty namespace-local `fuser` output alone is insufficient.
- An exclusive serial open, a process lease and verified access restrictions
  prevent competing unprivileged owners. Qualification must include both serial
  and raw USB access, pre-existing owners, access races, reconnect and crash.
  Trusted host root remains outside an adversarial security boundary.
- The ROS container gets only the scoped Unix socket. It receives no blanket
  host PID namespace, privileged container mode or raw device access.
- A provisioned pairing binds XIAO MAC E0:72:A1:FB:E5:DC, expected firmware,
  calibration and the inspected Maestro serial/settings. Maestro UART cannot
  independently return its USB serial number; pairing evidence must explicitly
  distinguish provisioning from live serial-number verification. Unplug/replug,
  replaced firmware or an uncertain pairing invalidates admission.
- The Maestro's separate USB interfaces must not provide a competing control
  path during a XIAO movement lease. Provisioning and diagnostics use a separate,
  mutually exclusive mode with no movement authority.

### USB/firmware command contract

- Use a versioned, length-bounded frame with session nonce, monotonic sequence,
  command identity and a matched reply. USB diagnostic/boot text is accepted only
  before the identity handshake, never as an active-run reply.
- Expose explicit query, arm, bounded target, stop and health operations; never
  arbitrary UART forwarding or a general-purpose parameter-write endpoint.
- Firmware and host bind the same calibration version and allowed channels.
  Reject unknown channels, non-finite/out-of-range values, replayed/stale packets,
  expired leases and requests outside the armed scope before transmitting.
- D2/GPIO3 is TX and holds UART idle high when inactive; D3/GPIO4 is RX. Preserve
  GPIO43/44 for audio. Boot, reset and fault states require real pin measurements.
- Every supported Maestro command carries CRC. Read responses have no Maestro
  CRC, so match expected response size/context and check controller status and
  readback; do not invent end-to-end response integrity.
- Do not automatically resend a movement after ambiguous completion. An error
  or timeout revokes the run and stops further target transmission.

### Error behavior needs explicit qualification

Pololu CRC-7 detects many command errors but is not a complete safety boundary:
responses have no CRC, and Mini SSC packets are exempt. Extra bytes therefore
remain a physical-link failure even if individual CRC queries succeed.

The Maestro currently sends outputs to their configured Home on an error.
That behavior must be reconciled with Alice's existing software fault policy,
which sends no recovery move after communication becomes uncertain. Recommended
behavior is to preserve the last commanded output on a communication fault,
using a separately backed-up and qualified controller error profile, while
keeping calibrated Home targets in the software manifest. Do not silently change
the controller's Home/error settings or claim that closing serial cuts power.
The operator's master switch remains the physical power-removal mechanism.

### ROS integration

- Add an explicit XIAO backend and its verified host-owner lease to admission.
  Keep direct-Maestro hardware rejection in place until separately qualified.
  No Boolean override may bypass the existing unavailable-hardware guard.
- Preserve epoch/generation, original timestamps, cancellation, the 250 ms
  age/progress limits, calibration identity and controller error checks.
- Distinguish actuator hardware from audio/perception modes. A first jaw-only
  ROS trial must not require enabling unrelated camera or speaker hardware, nor
  label real actuator output as simulation.
- Readback receipts describe controller PWM, not measured physical arrival.
  A bridge restart cannot resume an old ROS epoch or replay queued commands.
- Begin with read-only ROS telemetry. Admit the smallest calibrated jaw fixture
  only after ownership, transport, fault behavior and latency tests pass. Expand
  to the existing face channels 6/3/4/5/9/11 only through explicit scoped tests.

## Qualification sequence

1. Repair/shorten/reseat the physical UART path. Preserve failed captures and
   repeat matched sent/received byte trials at the chosen rate. Check actual
   pin levels, power/reset and single-USB behavior. CRC acceptance alone is not
   a clean-link result.
2. Implement/test the framed read-only bridge and host owner using fake serial
   devices first. Exercise startup text, short/extra/malformed replies, competing
   owners including raw USB, process exit, hotplug, lease replay and cancellation.
3. Add bounded movement encoding and calibration enforcement offline. Test
   damaged/stale/duplicate requests and ambiguous completion without a motor.
   Qualify the proposed controller error profile and restoration procedure.
4. Integrate ROS read-only telemetry, then exercise the actual container/socket
   topology and every fail-closed admission path. Retain the simulation baseline.
5. Run the attended jaw trial through the new path, using the user's standing
   authorization once the preceding checks pass. Record physical observations
   separately from PWM. Repeat no routine readiness/typed-confirmation checklist.

A clean bounded test is evidence for that configuration, not a guarantee of
zero future electrical errors. Record wire layout, firmware/config hashes,
selected baud, controller profile, error counts and latency for every trial.

## Primary references

- [Maestro CRC behavior](https://www.pololu.com/docs/0J40/5.d)
- [Maestro error/Home behavior](https://www.pololu.com/docs/0J40/4.e)
- [Maestro command formats](https://www.pololu.com/docs/0J40/5.e)
- [Existing ROS hardware restriction](../../architecture/0012-ros2-docker-runtime.md)
- [UART bench evidence](../../../hardware/xiao-maestro-uart-evidence-2026-09-16.md)
