# Direct Maestro UART and auto-baud test — 2026-09-18

## Result

The Maestro receives and executes UART commands, but the CSGY adapter still
receives no reply bytes. Auto-baud worked at 9600 in this test; auto-baud at
115200 produced no USB RX mirror. Fixed 115200 mirrored every tested byte.
This does not identify the failed component or prove an electrical cause.

## Setup and checks

- Mini Maestro 12, serial `00037376`; USB Command Port `/dev/ttyACM0` used only
  for RX capture, plus native USB settings/status reads. Auxiliary port `/dev/ttyACM1`.
- CSGY CH340 at physical USB `3-2.4`, currently `/dev/ttyUSB0`, sends commands.
  Operator reports direct 5 V TTL TX/RX wiring; no level shifter.
- XIAO is connected on `/dev/ttyACM2` but was not opened or changed.
- 8N1, no flow control. USB tty and raw USB ownership checks passed.
- Initial configuration: USB Chained, device 12, CRC off, timeout disabled.
  All 102 settings and 12 servo records matched the saved baseline.
- Initial native serial signal error `1` was saved and acknowledged before testing.
  All servo positions/targets were already at configured Home values. The guarded
  configuration helper rejected any state where reinitialization would change outputs.
- Offline checks verified the added auto/fixed no-CRC profile plans and rejection
  of non-home output state and unacknowledged errors. No production code changed.

## Trials

| Configuration | Sent query bytes (hex) | Maestro USB RX mirror | Adapter reply |
|---|---|---|---|
| Auto 115200, CRC off | `AA 0C 10 00`, `90 00`, `AA 0C 21` | None for all three | None |
| Fixed 115200, CRC off | Same three packets | Exact for all three | None |
| Auto 9600, CRC off | Same three packets | First `0C 10 00`; subsequent packets exact | None |

The first auto-baud `AA` was not mirrored at 9600. Subsequent correctly decoded
bytes establish baud detection. Each capture waited 250 ms. Native error status
remained zero and all servo records remained unchanged after each query.

The addressed Get Position packet is `AA` + device `0C` + command `10` + channel
`00`; the compact equivalent is `90 00`. Expected reply for channel 0 is
`50 19` (6480 quarter-microseconds). Both are documented query formats.

## Proof that the UART command parser executes commands

Using fixed 115200 with CRC temporarily enabled:

1. Sent Get Errors with deliberately invalid CRC `A1 1A`. USB RX mirror was
   exact; native error state changed from `0` to `8` (Serial CRC error).
2. Sent valid Get Errors `A1 1B`. USB RX mirror was exact; native error state
   changed from `8` to `0`, proving execution of this UART command. The expected
   response would be `08 00`, but the adapter received no bytes.

No native USB clear-error call occurred between those two packets. Native reads
used the read-only snapshot helper. All 12 servo records stayed unchanged.
The remaining failure is on the response side of the transaction; its physical
or software component cause remains unknown. A bad/ignored command alone does
not explain the valid Get Errors result.

## Final device state and next check

Left the board in **UART auto detect, CRC off, device 12, timeout disabled**.
Trained it at **9600 baud** with standalone `AA`, waited 250 ms, then sent
`90 00`: Maestro mirrored `90 00` exactly; adapter reply remained empty.
After a Maestro reset, send `AA` again to establish the baud rate. Fixed-baud
register 103 remains stored but is not the selected mode.

Final native error status is zero, script stopped, all servo records unchanged.
Only parameter 3 differs from the session's initial settings (1 → 2).
All opened serial ports were closed. No movement commands or firmware writes.
The initial USB Chained mode was intentionally not restored, per the user's
request to put the board in UART mode.

Requested multimeter readings of idle Maestro TX and adapter RXD relative to
common GND, with wiring unchanged, to check whether the return line is held low.
Operator replied "4.2" V and clarified that the points are connected. Treat
this as the idle voltage of the connected TX/RXD net; duplicate measurements
are unnecessary here. It is not held at ground. Idle DC cannot verify serial
pulses or the adapter input threshold. Next: bounded repeated read-only queries
while the operator watches the meter for a change; waiting for meter placement.

## Evidence and sources

- Private source, preflight backup, configuration requests/readback, individual
  transactions and final snapshot: `/home/alice/.local/state/alice-hardware/xiao-e072a1fbe5dc/uart-autobaud-20260918/`.
- Aggregate config, metrics and hashed manifest:
  `artifacts/respeaker/2026-09-18/direct-uart-autobaud-01/`.
- [Serial modes](https://www.pololu.com/docs/0J40/5.a),
  [command framing and AA](https://www.pololu.com/docs/0J40/5.c),
  [query formats](https://www.pololu.com/docs/0J40/5.e),
  [CRC rejection](https://www.pololu.com/docs/0J40/5.d),
  [error behavior](https://www.pololu.com/docs/0J40/4.e).

## Follow-up: low idle voltage, activity stream not run

Operator subsequently reported "0.8 now" before any UART query stream ran.
No UART bytes were transmitted during this follow-up. Both devices remained
on USB and ownership checks were clear. A native read found error 1 (serial
signal error), with all settings/servo records unchanged. This error is saved
and remains latched; it supersedes the earlier zero-error final state.

The prepared meter_activity.py stream was syntax-checked only. Its initial
py_compile failed to write the root-owned cache directory; in-memory compile
then passed. The script has not been hardware-tested or executed.

Asked operator to disconnect only Maestro TX -> adapter RXD and measure the
unloaded Maestro TX pin relative to GND, leaving other connections unchanged.
Awaiting result before another transmission. This is a loading/connection
comparison; no failed component is identified.

## Software-only continuation; fixed UART 9600 is current

The user directed continued software investigation with wiring unchanged,
superseding the wire-disconnect request and timed-meter activity proposal.
No additional physical confirmation was requested. The meter_activity.py
script remained unexecuted; the later 120-query burst was a different software
buffering test and had no synchronized meter reading.

Configured mode 3 (UART fixed), baud register 1249 (9600), CRC 0, with native
readback verification. The previously recorded serial error 1 was acknowledged
by the guarded configuration helper. No other controller settings changed.

- Fourteen queries tested compact/addressed Get Position, Get Errors, Moving
  State, and Script Status, all four DTR/RTS states, and 20 ms inter-byte spacing.
  Every RX mirror was exact; all native errors stayed zero; no adapter replies.
- Raw POSIX C independently configured 9600 8N1, CREAD/CLOCAL, no software or
  hardware flow control. It sent `AE`, `93`, `90 00`, `AA 0C 10 00`, with a
  one-second poll/read window each. Maestro USB serial ports stayed closed.
  All four had no reply, no syscall failure, and unchanged native state.
- Corrected usbmon capture retained only the adapter's USB transactions. It
  recorded all four outgoing packets, successful line-control write `00C3`
  (RX enable + TX enable + eight bits), and two queued bulk-IN requests.
  Neither returned payload; both were cancelled normally on close with status
  -2 and length 0. This is evidence before the tty/Python read layer, not a
  measurement of voltage or physical UART waveforms.
- A 120-query burst at 7 ms spacing asked all 12 positions repeatedly and then
  waited two seconds. All 240 command bytes mirrored exactly; 240 reply bytes
  were expected and zero arrived. Native errors stayed zero.
- The first usbmon helper stopped on EAGAIN. Its zero-line capture is unusable;
  only the subsequent 30-event successful capture supports USB conclusions.
  TIOCGICOUNT was unsupported. No counter-based claims are made.

Final state: **UART fixed 9600, CRC off, device 12, timeout disabled, errors 0,
script stopped, all 12 servo records unchanged.** Compared with the session's
initial USB Chained settings, only mode 1→3 and baud register 103→1249 differ.
All opened ports closed; the temporarily loaded usbmon module was unloaded.
No servo movement commands, firmware changes or default-settings reset.

Firmware descriptor reads **1.00**; official Mini firmware release is 1.03.
The published updates cover acceleration settling/error handling and macOS USB
compatibility, without explicitly identifying this absent UART reply symptom.
An upgrade is not an established fix and was not attempted.

Evidence: `artifacts/respeaker/2026-09-18/direct-uart-software-audit-01/`.
Private results/source: `software-audit-01/`, `posix-usbmon-01/`,
`burst-buffer-test-01/`, and `software-audit-final.json` under the existing
private `uart-autobaud-20260918` root.
References: [UART modes](https://www.pololu.com/docs/0J40/5.a),
[script status](https://www.pololu.com/docs/0J40/5.f),
[Linux CH341 driver](https://github.com/torvalds/linux/blob/master/drivers/usb/serial/ch341.c),
[firmware versions](https://www.pololu.com/docs/0J40/4.f).
