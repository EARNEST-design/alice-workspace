# Maestro command-port evidence — 2026-09-01

Scope: read-only identification of the USB Command Port for the connected
Pololu Mini Maestro 12. No TTY was opened and no serial bytes or actuator
commands were sent while gathering this evidence. Master servo power remained
off.

## Authoritative device behavior

The Pololu Maestro user's guide, section 5.a, states that the Maestro exposes a
Command Port and a TTL Port over USB. On Linux, the Command Port is usually
`/dev/ttyACM0` and the TTL Port is usually `/dev/ttyACM1`; the guide warns that
the numeric suffix can vary with other serial devices. The stable USB identity
must therefore be used in addition to the transient TTY number.

Source: <https://www.pololu.com/docs/0J40/5.a>

## Observed identity and mapping

Read-only `udevadm`, sysfs, and USB-descriptor inspection found exactly one
connected Maestro:

- Product: Pololu Mini Maestro 12-Channel Servo Controller (`1ffb:008a`)
- Serial: `00037376`
- USB interface `00`:
  `/dev/serial/by-id/usb-Pololu_Corporation_Pololu_Mini_Maestro_12-Channel_USB_Servo_Controller_00037376-if00`
  resolves to `/dev/ttyACM0`
- USB interface `02`:
  `/dev/serial/by-id/usb-Pololu_Corporation_Pololu_Mini_Maestro_12-Channel_USB_Servo_Controller_00037376-if02`
  resolves to `/dev/ttyACM1`

The official Linux ordering and the observed same-device interface mapping
together identify the stable `if00` path as this unit's Command Port. The
hardware adapter must still independently validate serial `00037376`, USB
interface `00`, and the resolved device immediately before a run. A fresh
operator preflight attestation remains mandatory; this document is evidence,
not run approval.

