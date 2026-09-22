# XIAO–Maestro UART bring-up, 2026-09-16

## USB devices disconnected; new UART selection not applied, 2026-09-16

After the operator shared the correct UART-mode diagram, proposed leaving
Maestro in fixed UART 115200/CRC for continued direct diagnostics. The attempted
explicit adapter Get Position test aborted during device preflight: ttyUSB1
was absent. Inventory confirms Maestro, CSGY adapter at USB 3-2.4, and XIAO
are absent; only HW-649 CH340 at USB 3-2.1/ttyUSB0 remains. **No settings or
commands were sent in this attempt. Last confirmed Maestro settings remain
the restored baseline USB Chained mode 1, CRC 0.** Do not claim UART was applied.
Asked operator to reconnect Maestro USB and CSGY adapter, preserving direct
UART wiring. The earlier requested multimeter readings remain unanswered.
Private failed-preflight log:
uart-crc-probe-20260916/direct-uart-explicit-route-20260916-01/result.json.

## UART mode verified; original settings fully restored, 2026-09-16

On USB reconnection, native readback confirmed Maestro 00037376 still had fixed
UART mode 3, baud register 103 (115200), CRC 1, errors zero and script stopped.
All parameters/servos matched the pre-unplug configured state. Thus the failed
standalone direct-adapter query used the intended UART mode; USB Chained was
used only for USB-originated transmit/loopback diagnostics, where its RX bytes
are mirrored without interpretation. Query `a1 1b` included the required CRC.

Then completed the promised restoration to original USB Chained mode 1,
baud register 103, CRC 0. Independent final native read verified all 102 original
parameters and all 12 servo records exactly, errors zero, script stopped.
**No restoration remains pending.** Maestro USB, external VIN and full direct
adapter UART/GND are connected; no loopbacks or level shifter. Adapter port is
closed. No motion commands or firmware writes. Future actual adapter commands
require configuring fixed UART again; do not send them expecting replies in
the currently restored USB Chained mode.

Both devices' local loopbacks passed, but direct reception remained silent with
both directions, one direction alone, slower baud/control-line variations,
USB-only power and Maestro USB absent. None establishes the component cause.
Asked which instrument is available (oscilloscope, logic analyzer, multimeter)
to inspect the signal at adapter RXD instead of continuing mode/wiring guesses.
Native evidence: direct-adapter-no-maestro-usb-20260916-01/reconnected-native.json,
restore.json and final-native.json under the private uart-crc-probe-20260916 root.
Reference: https://www.pololu.com/docs/0J40/5.a

## No-Maestro-USB test failed; original settings restoration pending, 2026-09-16

The operator restored external VIN 5 V and full adapter TXD->Maestro RX,
Maestro TX->adapter RXD/common-GND wiring, then unplugged only Maestro USB.
Verified no Mini Maestro USB device or command-port link was present. Through
adapter USB at physical path 3-2.4, sent CRC Get Errors `a1 1b` at 115200;
received no bytes before the 200 ms deadline. Stopped before position queries.
No USB RX mirror or native error read was possible in this configuration.

Removing Maestro USB did not recover communication. USB-only logic power had
also failed. These tests do not establish the electrical cause. Both devices'
local loopbacks previously passed; the direct return connection was silent.
No level shifter was present during any direct-adapter test.

**Current Maestro configuration remains fixed UART mode 3, baud register 103,
CRC 1. Restoration is PENDING.** Asked operator to reconnect Maestro USB,
leaving VIN/UART wiring unchanged, to capture native errors and restore the
original mode 1/baud 103/CRC 0 with all-parameter/servo comparison. No target
commands or additional firmware writes were sent; adapter port is closed.
Private results: uart-crc-probe-20260916/direct-adapter-no-maestro-usb-20260916-01/.
Aggregate: artifacts/respeaker/2026-09-16/direct-adapter-no-maestro-usb-01/.

## USB-only power failed; prepared test without Maestro USB, 2026-09-16

Operator removed external VIN power, leaving Maestro USB power and the same
Maestro TX -> adapter RXD/common-GND connection. The identical 115200 test still
received nothing at the adapter; native USB reply `50 19` was correct. All
original parameters/servo records remained unchanged at that test's end.
Removing external VIN alone therefore did not recover the return path.
Private result: uart-crc-probe-20260916/maestro-usb-only-power-20260916-01/.

**Current Maestro settings intentionally changed for the next test:** guarded
configuration to fixed UART mode 3, baud register 103 (115200), CRC 1 completed,
errors zero, script stopped, all 12 servo records unchanged. Original serial
settings restoration is now PENDING. There were no movement commands.
Preparation: uart-crc-probe-20260916/direct-adapter-no-maestro-usb-20260916-01/
contains configure.json and prepared.json with expected channel positions.

Asked the operator to reconnect external 5 V -> VIN and adapter TXD -> Maestro
RX, retain Maestro TX -> adapter RXD/common GND, then unplug only Maestro USB
while keeping adapter USB connected. Await completion before a bounded direct
CRC read through adapter USB. Confirm Maestro USB absent and adapter's physical
USB path 3-2.4 before transmitting; no USB mirror/identity read is available in
that state. Use prepared known-home values; stop on missing/incorrect/extra data.
Restore original mode 1/CRC 0 with native identity/state verification after the
operator reconnects Maestro USB. Keep private evidence; never select HW-649 at
3-2.1 by the CH340's non-unique by-id link.

Aggregate USB-only power evidence:
artifacts/respeaker/2026-09-16/maestro-usb-only-power-01/.

## Maestro-to-adapter-only path remains silent, 2026-09-16

The operator completed the single-direction wiring: Maestro TX -> adapter RXD
and common GND, adapter TXD disconnected, both local loopbacks removed, both
USB connections retained. At 115200, USB-originated Get Position 0 (`90 00`)
gave the correct native USB response `50 19`; no transmitted bytes reached the
adapter in 200 ms. Adapter transmitted no bytes.

Without rewiring, repeated at 9600 and 115200 across all four DTR/RTS states
at each rate. All eight native replies were correct; all eight adapter receive
windows were empty. This does not explain the failed connection, but changing
baud/control lines did not recover it. Both devices previously passed their
own local loopbacks. Both serial runtime baud settings were returned to 115200
and adapter DTR/RTS false. All 102 original Maestro parameters and all 12 servo
records remain exact, errors zero, script stopped. No servo commands, EEPROM
setting changes or firmware writes occurred in these one-direction tests.

Current wiring remains the single-direction connection above. The next useful
evidence is the exact multi-mode adapter hardware, pin labels and mode jumpers
(clear photograph); do not invent CSGY schematics or diagnose a failed component
from the name alone. The reported direct 5 V-to-5 V wiring has no level shifter.
Private results: uart-crc-probe-20260916/maestro-to-adapter-only-20260916-01/ and
maestro-to-adapter-settings-20260916-01/.
Aggregate: artifacts/respeaker/2026-09-16/maestro-to-adapter-only-01/.

## Both independent UART loopbacks pass, 2026-09-16

The operator confirmed both devices looped locally, separate from each other:
adapter TXD-RXD and Maestro TX-RX. At 115200 8N1 the adapter returned all ten
bytes exactly in three trials (30/30, no extras). In USB Chained mode, Maestro
received the USB Get Errors command `a1` and returned `00 00 a1` in each of
three trials: two-byte native zero-error reply plus the UART loopback byte.
Thus both devices demonstrated local transmit/receive operation. All original
102 Maestro parameters and 12 servo records remained unchanged; errors zero,
script stopped. No movement or settings changes were needed for this test.

The earlier adapter-only test also passed 40/40 bytes across four DTR/RTS
combinations. Direct inter-device communication remains unqualified: commands
reached Maestro, but no replies reached adapter RXD. No level shifter was
present in any of the direct adapter tests, per the operator's correction.
A component failure, wiring fault, loading/voltage problem or software cause
is not established by these local loopbacks.

Next physical step requested: remove both local loops; connect only Maestro
TX -> adapter RXD plus common GND, leaving adapter TXD unconnected. Keep both
USB cables connected. Then test USB-originated Maestro TX bytes independently
of its UART command parser. Await operator completion before sending.
Private result: uart-crc-probe-20260916/separate-loopbacks-20260916-01/result.json.
Aggregate: artifacts/respeaker/2026-09-16/separate-uart-loopbacks-01/.

## Adapter loopback passed; wiring history corrected, 2026-09-16

The operator clarifies that **all direct USB adapter tests used direct 5 V to
5 V connections, with no level shifter present from the beginning**. Earlier
statements about disconnecting an old shifter were an assistant interpretation
error. The two UART trials repeated the same wiring; they are not a controlled
before/after comparison. Preserve raw results; their wiring metadata is
superseded by operator-direct-adapter-wiring-correction-20260916.json.

The operator then confirmed the adapter-only TXD-to-RXD loopback, disconnected
from Maestro. At 115200 8N1, each of four DTR/RTS combinations returned all ten
transmitted bytes exactly: 40/40 bytes, no extra bytes. Termios had CREAD enabled,
canonical processing and local echo disabled, and no software/hardware flow
control. DTR/RTS were returned false. This demonstrates short adapter TX/RX and
host receive operation; it does not qualify sustained communication. No command
was sent to Maestro in this loopback test. Settings/parser review agrees with
Pololu's documented USB Chained and fixed-UART behavior; no software fix found.

Asked the operator to keep the adapter separate and jumper Maestro's own TX
and RX pins for a Maestro-only loopback through its USB Chained mode. Await
that physical step before transmitting. Existing Maestro baseline is restored;
no movement or firmware writes. Private result:
uart-crc-probe-20260916/direct-adapter-loopback-20260916-01/result.json.
Aggregate: artifacts/respeaker/2026-09-16/direct-usb-adapter-loopback-01/.

## Earlier adapter repeat (wiring interpretation corrected above), 2026-09-16

With the same direct TXD->RX, RXD->TX, GND->GND wiring,
the repeated 115200 CRC query still
failed: transmitted `a1 1b`, Maestro USB mirror exactly `a1 1b`, adapter reply
empty after a 200 ms read deadline. Native errors were zero. The planned
1200-position series stopped at this first query, before any position reads.
All 102 original parameters and all 12 servo records were restored exactly;
script stopped, errors zero. No movement commands were sent.

The repeat does not identify the cause of missing receive bytes.
At that stage, adapter RX operation and Maestro
TX electrical activity are not independently proven by this test. Asked the
operator to disconnect the adapter TXD/RXD leads from Maestro and join their
free ends for an adapter-only loopback; awaiting completion before transmitting.
Private results: uart-crc-probe-20260916/direct-adapter-uart-20260916-02/.
Aggregate evidence: artifacts/respeaker/2026-09-16/direct-usb-adapter-02/.

## Direct USB serial adapter test, 2026-09-16

The operator identifies the new adapter as CSGY USBTO485 with TXD/RXD pins,
assigns it to ttyUSB1 and requests a direct Maestro serial test. USB identity
is CH340 1a86:7523, physical path 3-2.4. HW-649 is the other CH340 at 3-2.1
(ttyUSB0). Both lack unique USB serials: their shared by-id link now points to
ttyUSB1. Use by-path plus physical identity; never flash the shared by-id link.

At 115200 8N1, adapter -> Maestro transmitted `90 00` exactly, verified by the
Maestro USB receive mirror in USB Chained mode. The reverse test sent `90 00`
through Maestro USB: native position reply was the correct `50 19`, but the
adapter received no bytes. A guarded fixed-UART/CRC trial then transmitted
`a1 1b` exactly (USB mirror verified); adapter received no response in 200 ms,
native controller errors remained zero. Thus transmit is demonstrated for two
short packets; bidirectional communication and sustained reliability are not.
The operator subsequently clarified that no shifter was connected in any of
these direct adapter tests. The results do not identify the failed component
or independently measure the reported 5 V logic levels.

The first preflight found serial-signal error 1, with all original parameters
and servo records unchanged; it sent no serial command. This recorded serial
fault was acknowledged using the existing guarded baseline procedure before
testing. Final verification restored all 102 parameters and all 12 servo
records exactly to the original backup, errors 0, script stopped. No target
commands, firmware writes or production ROS changes occurred.

Private results: uart-crc-probe-20260916/direct-adapter-rx-20260916-01 and -02,
direct-adapter-tx-20260916-01, direct-adapter-uart-20260916-01.
Aggregate config, metrics and manifest:
artifacts/respeaker/2026-09-16/direct-usb-adapter-01/.
Next diagnostic target is the Maestro TX -> adapter RXD path, isolated from
the old translator. Existing XIAO firmware/ROS qualification remains unfinished.

## Conclusion

Latest isolation: the extra byte disappears when either the Maestro TX return
wire or the low-side wire to XIAO D3 is disconnected, and returns with the full
path connected. It occurs with the XIAO UART driver enabled and removed. A
single-USB test also failed on the first position query. The specific component
cause remains unproven. Maestro is restored, XIAO disarmed, no movement sent.
The operator's latest channel correction is D2 -> A3 and D3 -> A1; previous
channel numbers in raw logs followed requested moves and are unverified.
They prefer trying another board next; its identity is pending.

Latest: after the operator shortened the cables, CRC-protected trials at 115200,
38400 and 9600 all failed on the initial Get Errors query: `a1 1b` arrived as
`a1 1b ff`, with a correct zero-error reply. No position query was reached.
The specific electrical cause remains unconfirmed. All original settings and
servo records were restored and verified. The current firmware is
`alice-uart-crc-readonly-2`, superseding the original probe described below.
See [ROS/CRC follow-up](../docs/experiments/2026-09-16-xiao-ros-integration.md).
Maestro is restored to its original USB Chained profile; movement is not enabled.

The single-USB path is functional but **not reliable**. After isolated tests
passed, 709 matching replies were read through XIAO USB with Maestro USB
unplugged, followed by a timeout. A later trace identified command corruption:
`90 01` reached the Maestro as `90 37`. No target command was sent. Maestro's
original USB Chained mode and all recorded settings have been restored; XIAO
transmission is disabled pending supply/wiring checks. Earlier passive results
below remain historical evidence, followed by the active-test results.

## Hardware and test configuration

- XIAO ESP32S3 on ReSpeaker Lite: USB 303a:1001, E0:72:A1:FB:E5:DC.
- Mini Maestro 12: USB 1ffb:008a, serial 00037376, native USB available.
- Operator-reported wiring: Maestro RX -> translated D2/GPIO3;
  Maestro TX -> translated D3/GPIO4; translator WCMCU-401.
- WCMCU-401 is sold as a TXB0104 module; exact chip/board revision, OE
  connection, measured supply levels and physical soldering remain unverified.
- Initial/final Maestro mode 1 (USB Chained), CRC disabled, device number 12,
  timeout disabled, script stopped. Command port opened at 115200 baud.
- XIAO UART1/2 listen at 115200 8-N-1, GPIO3/4 inputs with no internal pulls.
- Speaker physically disconnected by the operator after a loud reset-time
  noise. The new app suppresses runtime logging, holds audio data GPIO43 low
  and requests XMOS PA mute. Reset/ROM behavior is not qualified by these steps.

## Firmware, backup and command restrictions

The previous app was `alice-wifi-diagnostic-1`. A completed 1 MiB readback
contains its whole 871840-byte app and matches the saved app byte-for-byte.
The partition-table bytes also match. An earlier attempted 8 MiB readback was
interrupted by a reset and produced no completed backup; its log is retained.

Only the application at flash offset 0x10000 was replaced. Esptool reported
successful hash verification for the 314672-byte `alice-uart-readonly-probe-1`
image. NVS, bootloader and partition table were not written. The probe has
no credentials, Wi-Fi service or I2S audio transport. The prior backup may
contain credentials and remains private outside Git.

The command encoder accepts exactly `READ 0` through `READ 11` and generates
only Maestro Get Position packets (0x90, channel). Native C++ tests were
observed failing before implementation and passing afterward. Device tests
rejected raw/motion commands, an out-of-range channel, a read while unarmed,
an embedded NUL and an oversized line. The transmitter is input-only until an
explicit ARM command; none was issued in the initial passive trial. Follow-up
trials used ARM RX4, mapping RX to GPIO4 and TX to GPIO3. Idle/failure handling
releases both pins; a future bridge needs defined inactive-line behavior.

## Initial passive measurements

In USB Chained mode the Maestro mirrors command-port requests onto its TX
pin, while also returning its own responses to USB. Six read-only requests
for channels 0, 2, 4, 6, 8 and 10 produced:

| Signal | Captured bytes (hex) |
| --- | --- |
| Sent via Maestro USB | `90 00 90 02 90 04 90 06 90 08 90 0a` |
| D3 / GPIO4 | `90 00 90 02 90 04 90 06 90 08 90 0a` |
| D2 / GPIO3 | `90 00 90 02 7e 90 08 90` |

Both input levels were high after capture; neither capture overflowed. The
host's original `usb_positions` field is **invalid evidence of positions**:
the stream includes bytes echoed from Maestro RX, interleaved with responses.
For example, 144 is 0x0090, matching a request rather than the expected channel
position. Preserve the original capture but do not use that field as a result.

Independent native USB snapshots before and after confirm all 102 recorded
parameters, positions, targets, speeds and accelerations unchanged. Positions
and targets were `[6480,6007,7440,5626,6173,5918,5059,0,6000,6499,6000,5524]`
in quarter-microsecond units. These are controller pulse settings, not measured
physical joint positions. Errors changed from 0 to 1 (bit 0, serial signal);
the script stayed stopped. Native GET_VARIABLES reads clear performance flags,
but no explicit error-clear request was sent.

The operator subsequently reported 28 MOhm between D2 and D3, arguing against
a direct short. This does not measure high-frequency coupling or verify the
translator's supply voltages.

## Follow-up: idle-level and directional tests

In unchanged USB Chained mode, ARM RX4 held D2 at UART idle. Twelve read-only
USB queries then matched all native values exactly, without the earlier echo.
A Get Errors request acknowledged zero errors. A XIAO-generated `90 00` then
arrived exactly at Maestro RX, observed through its USB mirror. The XIAO
correctly timed out waiting for a UART reply because USB Chained mode does
not answer commands received on RX. Controller errors stayed zero and settings
and servo records were unchanged. This supports pickup on the undriven line
as the cause of the original passive anomaly.

## Follow-up: direct UART and single-USB trials

A tested guard required the known device, zero errors, stopped script, unchanged
baud/CRC configuration, and every channel already at its configured home before
reinitializing. It changed only parameter 3 (serial mode), from 1 to 3, then
issued the documented reinitialize request. Readback confirmed that one change
and unchanged servo state. Fixed-baud register 103 was retained (~115385 baud,
paired with XIAO 115200); no calibration or target parameter was written.

| Trial | Recorded result |
| --- | --- |
| Both USB cables, native state checks between sweeps | 22 complete sweeps / 264 recorded matching positions; next sweep timed out at channel 4 |
| Maestro USB unplugged, XIAO USB only | 709 matching positions; next query, channel 1, timed out |
| Maestro USB reconnected, RX mirror recorded | First query correct; second request `90 01` appeared as `90 37`, with returned value zero |

Matching replies had approximately 6 ms median round-trip time. These are
short diagnostic runs, not reliability acceptance. The first trial completed
four further comparisons before its failure but did not persist that partial
sweep, so only 264 replies are counted as recorded evidence. Both timeouts
reported zero received reply bytes. Single-USB firmware did not expose an
error-register read; no zero-error claim is made for that interval.

The final mirror trace localizes observed corruption to the XIAO-to-Maestro
command path: the device processed an unintended channel number, rather than
merely returning a damaged position value. Native USB still reported zero
errors and unchanged servo outputs. Valid-looking byte corruption can evade
the Maestro's error flags; future motion transport needs an appropriate
integrity check, in addition to fixing physical signal quality.

The failures with Maestro USB absent establish that its USB cable is not
necessary for the fault. Replugging it also changed host traffic and physical
conditions; these trials do not identify a ground loop as the cause.

Transmission stopped on each anomaly. The same guarded native procedure
restored mode 1, with final verification that all 102 parameters and 12 servo
records match the original backup, errors are zero, and the script is stopped.
Both USB cables are currently connected; XIAO reports unarmed.

Next required evidence: translator 3.3 V and 5 V supply voltages relative to
common ground, Maestro VIN, approximate wire lengths and translator-ground
connection. TXB0104's limited load drive/short-trace requirements make wiring
and supply checks relevant; the specific physical cause is not established.
Keep the speaker disconnected.

Six offline Python tests passed for rejection of interleaved/extra serial
bytes and unsafe mode changes. They were observed failing before implementation.
Native C++ query-encoder tests also remain passing. These tests validate the
diagnostic restrictions, not electrical reliability.

## Artifacts and current state

Private experiment root:
`/home/alice/.local/state/alice-hardware/xiao-e072a1fbe5dc/uart-probe-20260916/`.
It contains source, build, tests, baseline and post-test snapshots, passive
capture, flash command/log and private readback. Aggregate configuration,
metrics and hashes are under `artifacts/respeaker/2026-09-16/uart-probe/`.

The same UART diagnostic remains installed, passively listening; it does not
provide the previous Wi-Fi heartbeat or audio features. Maestro is restored
to USB Chained. Single-USB readback is demonstrated, but reliable transport,
Wi-Fi control and motion behavior remain unqualified. No commits or production
adapter changes were made.

## Primary references

- [Pololu serial modes](https://www.pololu.com/docs/0J40/5.a)
- [Pololu serial commands](https://www.pololu.com/docs/0J40/5.e)
- [Pololu SDK USB protocol definitions](https://github.com/pololu/pololu-usb-sdk/blob/master/Maestro/Usc/Usc_protocol.cs)
- [TI TXB0104 datasheet](https://www.ti.com/lit/ds/symlink/txb0104.pdf)
- [Seeed XIAO pin mapping](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/)
- [ReSpeaker Lite schematic](https://files.seeedstudio.com/wiki/SenseCAP/respeaker/respeaker_lite_v1.0_sch_1.png)
