# XIAO CRC qualification and ROS baseline, 2026-09-16

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

## Outcome

The existing eight-participant ROS simulation passed. Physical integration is
not complete: actual UART corruption persisted at three rates, despite correct
CRC generation. The shorter-cable repeat subsequently failed on its initial
query at all three rates, each with an extra `ff`. No movement-capable firmware, production adapter, ownership
override or Set Target command was introduced.

The operator selected completing XIAO/ROS integration before movement. Reported
rails are 3.3/5 V; all grounds and the 5 V supply join a breadboard. Wire length,
loaded VIN, decoupling and transient signal levels are not measured. The operator
subsequently reported shortening the cables; the repeat is recorded below. The speaker
remains unplugged after the earlier reset-time audio fault.

## ROS run

Used existing pinned images with `infra/ros2/deploy.py`, fresh project
`alice-xiao-integration-20260916`, and output
`artifacts/ros2/2026-09-16/xiao-integration-baseline/`.
Executed `up`, the default `run`, then `down`. All eight participant terminals
report success, no error and hardware=false. The action returned 31200 played
samples in the simulated audio path. This was not speaker playback or a servo
trial. The simulated Maestro runtime finished and confirmed simulated Home.

Run ID `run-b9570a1da34c4a8bb8fee8a180611044`, generation `authored-demo-29`;
default deterministic fixture/seeds remain those of the existing launcher.
Deployment and participant manifests retain image/config/calibration identity.
The existing hardware-admission rejection remains unchanged.

## Read-only CRC probe

Private root:
`/home/alice/.local/state/alice-hardware/xiao-e072a1fbe5dc/uart-crc-probe-20260916/`.
The Arduino build produced a 314384-byte app; esptool verified its flash write
at 0x10000. The partition table matches the preserved backup, and NVS/bootloader
were not written. The installed app is `alice-uart-crc-readonly-2`.

The probe accepts only exact position queries for channels 0–11 and Get Errors,
with a CRC byte generated for every packet. ARM enables the fixed TX GPIO3 / RX
GPIO4 mapping at one of three explicit rates. STOP, idle timeout, UART faults,
short/extra replies and nonzero controller errors disarm transmission. GPIO3
is configured output-high while inactive; there is no arbitrary UART forwarding,
Set Target, Wi-Fi or I2S streaming implementation. XMOS PA mute is requested.

Diagnostic limitation: `tx_level` calls gpio_get_level while the inactive pin
is output-only, which returns zero when input is disabled. It is not physical
voltage evidence; use a meter/scope or enable input readback in a future probe.
The first host attempt also encountered queued ROM boot text before the JSON
handshake and stopped before changing the Maestro profile. It is retained as
`crc-115200-1200.json`. The host then drained startup text before its identity
handshake; active-run replies remain strict JSON.

Native C++ tests were observed failing before implementation, then passing:
Pololu's published CRC vector `83 01 -> 17`, all single-bit mutations of the
12 query payloads, the previously observed `90 01 -> 90 37` corruption, and
rejection of motion/malformed commands without output mutation.
Three Python tests similarly exercise exact profile writes, home/script guards
and restricted acknowledgment of recorded serial error bits. All passed.

## Actual electrical trials

The host changed only Maestro serial mode, fixed-baud register and CRC enable,
then reinitialized. Before each change it required the expected identity,
stopped script and all outputs already at their configured homes. It checked
all other settings against the original snapshot. Commands received at Maestro
RX were independently observed through its USB Command Port mirror.

| Rate | Accepted position queries before fault | First recorded anomaly | Native errors |
| --- | ---: | --- | ---: |
| 115200 | 116 | `90 08 29` received as `90 08 29 ff` | 0 |
| 38400 | 192 | Get Errors `a1 1b` received as `e1 1b`; no reply | 8 (CRC) |
| 9600 | 227 | `90 0b 7b` received as `90 0b 7b ff` | 0 |

The host also sent CRC-protected Get Errors between sweeps. The position-query
counts are successfully validated prefixes of runs that stopped on the first
anomaly; they are not an estimated error rate or successful reliability runs.

Each trial saved the fault and native snapshot, stopped XIAO transmission and
restored the original mode=1, baud register=103 and CRC=0. Acknowledgment of the
observed CRC error was logged before restoration. All 102 recorded settings and
12 servo records matched the original afterward. No target command was sent.

## Repeat after shortening the cables

The operator requested another test after shortening the cables. Exact lengths
remain unmeasured. Maestro USB was reconnected for independent RX byte capture.
The same firmware, host runner, query order and 1200-position-query target were
used at 115200, 38400 and 9600; no firmware was flashed or production code changed.

All three trials stopped on the initial Get Errors request, before any position
query: transmitted `a1 1b`, Maestro USB RX mirror `a1 1b ff`. Each reply was
`00 00` (zero errors), and the native error register also read zero. The valid
reply does not remove the unexpected byte. This result does not establish a
statistical change in error rate or prove which electrical component is at fault.

Each run disarmed XIAO and restored the original controller settings. An additional
native snapshot at 2026-09-16T10:08:27Z verified all 102 parameters and 12 servo
records against the original backup, errors=0 and script_done=1. No Set Target
command was sent. Private results live under
`uart-crc-probe-20260916/short-cables-20260916-01/`; config, metrics and a hash
manifest for 12 private files are in
`artifacts/respeaker/2026-09-16/uart-crc-short-cables-01/`.

The next diagnostic is to inspect the actual translator chip, OE connection and
wiring, and capture the outbound signal before/after translation if a scope is
available. Bypassing the outbound translator with 3.3 V is not a qualified
replacement: Pololu specifies above 4 V for guaranteed Mini Maestro RX reception.

## Requested translator channel changes and USB-originated read

**Channel-label correction:** after these comparisons, the operator clarified
that D2 is on A3 and D3 is on A1. Earlier raw channel fields followed requested
moves; they are not independently verified physical assignments. Preserve those
records, but do not claim an exhaustive comparison of translator channels.
Logical TX/return disconnections are operator-reported.

The operator confirmed common breadboard 5 V/GND, outbound D2 -> A4/B4 ->
Maestro RX, and return Maestro TX -> B3/A3 -> D3. They moved only the outbound
channel to A1/B1. With unchanged firmware and runner, the 115200 trial again
failed on the first Get Errors query (`a1 1b ff`, reply `00 00`), before any
position read. Original controller settings and all servo records were restored.

A second comparison used the original USB Chained profile and held XIAO TX at
UART idle (ARM 115200, with no XIAO READ/ERRORS commands). A Get Errors byte
`a1` was sent through Maestro USB, which also transmits it on Maestro TX in
this mode. Expected native USB response: `00 00`; observed: `00 00 ff`.
The capture window was 50 ms. No bytes appeared in the separate 25 ms windows
before ARM, after ARM or after STOP. The run stopped after this anomaly.
All original parameters and servo records matched at completion, controller
errors=0, script stopped, XIAO disarmed. No Set Target command was sent.

This rules out XIAO query encoding as a necessary cause of the extra byte. It
does not yet distinguish electrical coupling, translator behavior, controller
behavior or capture-path behavior. The outbound channel change did not resolve
it. Moving the return channel from A3/B3 to A2/B2 also reproduced the same
first-query anomaly at 115200, with original settings/servos restored afterward.
The encoder tests were rerun successfully; source inspection confirms an
ERRORS request passes exactly two bytes to uart_write_bytes. This is source/test
evidence, not a scope measurement of the actual TX waveform.

Private captures: `uart-crc-probe-20260916/channel1-20260916-01/` and
`uart-crc-probe-20260916/channel1-usb-source-20260916-01/`.

### Return-wire disconnect/reconnect and single-USB comparison

With outbound A1/B1 and return A2/B2, the operator disconnected only Maestro
TX -> B2. The same USB-originated read method produced exact replies:
Get Errors `a1` -> `00 00`, Get Position 0 `90 00` -> `50 19`. Reconnecting
Maestro TX -> B2 reproduced `00 00 ff` on Get Errors. Idle capture windows
remained empty. XIAO sent no UART packets in either test. Both runs verified
all original parameters/servo records, errors=0 and script stopped afterward.
This controlled comparison establishes a dependency on the connected return
path; it does not identify whether the relevant coupling is in the translator,
XIAO receive-side connection, supply or another electrical element.

At the operator's request, the Maestro was then configured to fixed 115200 UART
with CRC and its USB cable removed. XIAO Get Errors returned zero, but the first
position query READ 0 timed out (zero received bytes, about 156 ms). The probe
was stopped. There was no independent Maestro RX mirror in this test, so it
cannot show which command bytes actually arrived. The fault also occurs with
Maestro USB absent. The operator reconnected USB; all 102 original parameters
and 12 servo records were restored, with zero errors (`restore.json`).

Private folders: `channels12-20260916-01/`,
`return-disconnected-usb-source-20260916-01/`,
`return-reconnected-usb-source-20260916-01/`,
`single-usb-channels12-20260916-01/`, all under the CRC probe root.
The reusable `usb_source_trial.py` records the USB comparison; the separate
`single_usb_trial.py` requires Maestro USB absence and records that the mirror
is unavailable. Both are bounded diagnostic scripts; production ROS remains
unchanged. Disconnecting only the low-side wire to D3, while Maestro TX remained
connected to the translator, again produced exact native replies `00 00` and
`50 19`. This narrows the dependency to the complete return path reaching the
XIAO input, without identifying the component responsible.

### UART disabled/enabled comparison

After reconnecting D3 and receiving the channel correction above, the host sent
Get Errors through Maestro USB in four alternating states: STOP, ARM, STOP,
ARM. STOP deletes the hardware UART driver, holds GPIO3 high as an ordinary
output and configures GPIO4 as a floating input. ARM uses the pins as UART
TX/RX while sending no XIAO UART packets. All four USB replies were `00 00 ff`.
The 25 ms settling/stop capture windows were empty. Active UART receive
processing and query encoding are not necessary to reproduce the extra byte.
The proposed boot buffer flush does not address this observed runtime dependency;
firmware already flushes UART RX on ARM and the host drains startup USB text.
No firmware change was made.

The final snapshot matched all original parameters and servo records, errors=0
and script_done=1; XIAO stopped. Both USB cables and the return path are connected.
New captures: `xiao-rx-disconnected-usb-source-20260916-01/` and
`uart-enable-comparison-20260916-01/`. Aggregate follow-up evidence lives in
`artifacts/respeaker/2026-09-16/uart-translator-channels-01/`.

A return isolation using a 2.2 kOhm / 3.3 kOhm divider (nominal 3.0 V from a
5.0 V signal) was proposed, but the operator prefers substituting another
board first. Its identity is pending: another 3.3 V MCU/USB-UART source versus
another translator module. No divider has been assembled or tested. At VDD=3.3 V,
Espressif specifies a GPIO high threshold of 0.75*VDD=2.475 V and maximum high
voltage VDD+0.3 V. Actual levels/tolerances must remain within these bounds.
Keep the upward translation on XIAO TX.

## Implications and next work

CRC demonstrably rejected one damaged packet. It did not eliminate physical
corruption. Maestro responses have no CRC, and its Mini SSC commands are exempt;
an extra `ff` is therefore not something to ignore in a movement transport.
Pololu also documents Home behavior on errors, which must be reconciled with
Alice's software rule against recovery moves after uncertain communication.

The [proposed architecture](../superpowers/specs/2026-09-16-xiao-ros-hardware-design.md)
uses a single host USB owner, a restricted ROS socket and bounded XIAO commands.
It includes complete ownership evidence, device pairing/provisioning limitations,
calibration, expiry/cancel checks, controller error behavior and staged jaw-first
qualification. It was presented for approval; implementation has not begun.

Raw diagnostic sources/builds/captures stay private. Aggregate metrics, config
and a hash manifest are under `artifacts/respeaker/2026-09-16/uart-crc-probe/`.

## Sources

- [Pololu CRC and response limitations](https://www.pololu.com/docs/0J40/5.d)
- [Pololu error/Home behavior](https://www.pololu.com/docs/0J40/4.e)
- [Pololu command formats](https://www.pololu.com/docs/0J40/5.e)
- [Mini Maestro RX voltage requirement](https://www.pololu.com/docs/0J40/1.b)
- [Espressif GPIO input-readback requirement](https://docs.espressif.com/projects/esp-idf/en/v5.5.1/esp32s3/api-reference/peripherals/gpio.html)
- [ESP32-S3 DC characteristics, section 5.4](https://www.espressif.com/sites/default/files/documentation/esp32-s3_datasheet_en.pdf)

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
The old shifter signal wires were reported still connected. Their removal and
adapter TTL voltage specifications remain unconfirmed. This is not an isolated
comparison and does not identify the failed component or prove pin voltages.

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


## Adapter repeat with old shifter disconnected, 2026-09-16

After the operator confirmed the requested direct TXD->RX, RXD->TX, GND->GND
wiring with old shifter signals removed, the same 115200 CRC query still
failed: transmitted `a1 1b`, Maestro USB mirror exactly `a1 1b`, adapter reply
empty after a 200 ms read deadline. Native errors were zero. The planned
1200-position series stopped at this first query, before any position reads.
All 102 original parameters and all 12 servo records were restored exactly;
script stopped, errors zero. No movement commands were sent.

This repeat does not support the old shifter connection as the sole cause of
the direct adapter's missing receive bytes. Adapter RX operation and Maestro
TX electrical activity are not independently proven by this test. Asked the
operator to disconnect the adapter TXD/RXD leads from Maestro and join their
free ends for an adapter-only loopback; awaiting completion before transmitting.
Private results: uart-crc-probe-20260916/direct-adapter-uart-20260916-02/.
Aggregate evidence: artifacts/respeaker/2026-09-16/direct-usb-adapter-02/.


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
