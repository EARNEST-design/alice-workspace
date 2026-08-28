# Hardware inventory — 2026-08-28

Read-only host discovery found:

- Pololu Mini Maestro 12-Channel USB Servo Controller
- USB VID:PID `1ffb:008a`
- Serial `00037376`
- USB path `1-3`; bus 1, device 17 at discovery time
- Driver `cdc_acm`
- Interface 00: expected `/dev/ttyACM0`
- Interface 02: expected `/dev/ttyACM1`
- Expected stable identifiers:
  - `/dev/serial/by-id/usb-Pololu_Corporation_Pololu_Mini_Maestro_12-Channel_USB_Servo_Controller_00037376-if00`
  - `/dev/serial/by-id/usb-Pololu_Corporation_Pololu_Mini_Maestro_12-Channel_USB_Servo_Controller_00037376-if02`

The execution sandbox did not expose the `/dev` nodes, but sysfs/udev metadata identified them. No separate USB motor driver was enumerated. Drivers connected downstream of Maestro outputs cannot be identified by USB inspection.

No device commands were issued.

## Before actuation

- Identify each driver board from labels/photos and obtain its datasheet.
- Trace power, ground, signal, and load wiring.
- Record every Maestro channel and safe pulse range.
- Establish current limiting, emergency stop, neutral pose, and physical clearance.
- Test one disconnected/unloaded channel at a time under a reviewed procedure.

