# XIAO / ReSpeaker Lite to Mini Maestro 12 over UART

Status: single-USB readback demonstrated, but UART corruption unresolved,
2026-09-16. Maestro's original USB mode has been restored.
The original proposal and earlier assembly notes below are historical.

## Latest bench result: 2026-09-16

Subsequent isolation ties the extra byte to the complete Maestro TX -> XIAO D3
return path. The anomaly occurs even with the XIAO UART driver removed, and a
test without Maestro USB also failed. The exact component is not identified.
Latest reported mapping: D2 -> A3, D3 -> A1; corresponding B channels inferred.
Earlier requested channel numbers in captures are uncertain after this correction.
Both USB cables/full return path are connected; original Maestro settings restored,
XIAO disarmed. The operator prefers another-board substitution next; await model.

Subsequent CRC-protected trials failed at 115200, 38400 and 9600. The XIAO now
runs `alice-uart-crc-readonly-2`, which only issues position/error queries.
The operator reports 3.3/5 V rails and common ground/5 V on a breadboard.
After shortening the cables, all three rates failed immediately: Get Errors
`a1 1b` arrived as `a1 1b ff`, with a correct zero-error reply. Inspect the exact
translator chip, OE and wiring next; the component responsible is not established.
Original Maestro settings were restored after every
trial. See the [CRC/ROS follow-up](../../docs/experiments/2026-09-16-xiao-ros-integration.md).

The operator identifies the translator as WCMCU-401, a module family sold
with TXB0104. Its actual chip marking, OE connection and rail voltages have
not been independently inspected. For TXB0104, A/VCCA is the low-voltage
side and B/VCCB the high-voltage side; VCCA must not exceed VCCB. See the
[TI datasheet](https://www.ti.com/lit/ds/symlink/txb0104.pdf).

The operator reports Maestro RX -> D2 and Maestro TX -> D3, through separate
translator channels. Their 28 MOhm D2-to-D3 measurement argues against a
direct short. Holding D2 at UART idle removed the initial passive echo, and
isolated tests passed in both directions. Do not swap the signal wires.

The Maestro was temporarily set to fixed UART. The XIAO returned 709 correct
readings with Maestro USB unplugged, then a timeout. A subsequent USB mirror
trace caught `90 01` arriving as `90 37`, demonstrating corruption on the
XIAO-to-Maestro command path. The link is not ready for motion. Translator
rail voltages, Maestro VIN, wire lengths and common ground are being checked.

The XIAO now runs `alice-uart-readonly-probe-1`, with no Wi-Fi/audio streaming,
and the speaker is physically disconnected following a loud reset-time fault.
The diagnostic rejects motion commands; it transmitted only read queries.
Maestro mode was restored to USB Chained and all recorded parameters and
servo state match the original backup. The XIAO transmitter is now disabled.
No production bridge or motion test is complete.
See [experiment evidence](../xiao-maestro-uart-evidence-2026-09-16.md).

## Earlier operator assembly notes: 2026-09-16

The operator reports the power-parts order is placed and the 3.3 V / 5 V
level-shifter board is assembled. They now want to wire a XIAO USB-to-Maestro
UART path, and mention VCC, 3V3 and A1/B1, A2/B2 labels. The actual translator
model, which board bears the power labels, and A/B supply assignment remain
unidentified. Do not assume the in-hand board is Pololu #2595: that example
uses LV/HV and L/H labels. The operator was asked for model/chip and power-pin
labels before confirming the physical A/B connections or any OE/DIR needs.

Both voltage domains need the correct supply/reference for a dual-supply
translator: 3.3 V on the XIAO side and 5 V on the Maestro side, with common
logic ground. VCC alone does not establish a 5 V rating or whether a 3V3 pin
is an input or output. ReSpeaker schematic page 1 was visually rechecked:
the J4 breakout's pin 1 is +3V3, pins 2/3 are ground, and pins 5/6 expose
XIAO_D2/D3. It does not provide a 5 V rail on that breakout. The XIAO itself
has a separate 5 V pin. Verify the actual board revision and pad labels.

Signal allocation remains GPIO3/D2 = UART TX and GPIO4/D3 = UART RX, keeping
GPIO43/44 free of UART use because they carry audio. The operator proposes
translator channel 1 for XIAO RX and channel 2 for XIAO TX; for a compatible
bidirectional translator this is electrically equivalent to swapping the
channel numbers in the earlier example below. Logical paths would be:
Maestro TX -> channel 1 high side -> channel 1 low side -> XIAO D3/RX;
XIAO D2/TX -> channel 2 low side -> channel 2 high side -> Maestro RX.
Physical A/B labels are still pending identification.

The last accepted checkpoint restores alice-wifi-diagnostic-1, which does
not implement this bridge. XIAO USB serial-to-UART firmware and Maestro UART
configuration are still required; no automatic USB forwarding is implied.
Use the USB connector on the XIAO for this route, not the XMOS audio USB
connector. Maestro logic still needs its own valid supply (its USB may remain
for initial configuration/power); UART signal wires do not provide power.
No serial port was opened, firmware flashed, settings changed or actuator
command sent during this wiring review.

## Architecture

External Alice computer -> Wi-Fi -> XIAO ESP32-S3 -> logic-level UART ->
Mini Maestro 12 -> existing face servo connections.

The XIAO can use I2S for ReSpeaker audio alongside a hardware UART for the
Maestro. The Maestro generates servo pulses; the XIAO passes validated
position/speed commands and reads controller replies. The host still supplies
Alice's higher-level speech and behavior processing. Audio transport and
motion timing integration require new firmware/host work; the installed
`alice-wifi-diagnostic-1` application does not control the Maestro.

## Pin allocation from the manufacturer schematic

The ReSpeaker carrier exposes `XIAO_D2` and `XIAO_D3` on its breakout pads.
Solder a flexible pigtail to those pads and GND, with a small removable
connector. This avoids soldering directly to the ESP32 package. Verify pad
labels/orientation on the physical board; header pitch is not established.

| Proposed connection | Signal path |
| --- | --- |
| `XIAO_D2` = GPIO3, assigned UART TX | XIAO -> 3.3 V-to-5 V translator -> Maestro RX |
| `XIAO_D3` = GPIO4, assigned UART RX | XIAO <- 5 V-to-3.3 V translator <- Maestro TX |
| GND | Common XIAO, translator and Maestro logic ground |

Use a spare hardware UART with explicit pin assignment. For example, Arduino
`Serial1.begin(115200, SERIAL_8N1, 4, 3)` selects RX GPIO4 and TX GPIO3. This is
an illustrative configuration, not installed actuator firmware.

Do not use the XIAO's default D6/TX and D7/RX pins: the carrier connects these
to I2S audio data. D4/D5 carry I2C; D8/D9/D10 carry I2S clocks; D0 controls the
RGB LED and D1 connects to the XMOS reset signal. `BUT_A` is a distinct net
from D2/D3 in the schematic.

GPIO3 can select the JTAG interface at reset when the corresponding eFuse is
enabled. A read-only check on this XIAO found `STRAP_JTAG_SEL=False`,
`DIS_USB_JTAG=False`, and `DIS_PAD_JTAG=False`; GPIO3 strap selection is not
enabled on this unit. No eFuse was changed. Still qualify reset/power-up
behavior with the chosen translator and actual wiring before motion.

## Voltage conversion and power

Use translation in both directions. Pololu says the Maestro's RX is not
guaranteed to recognize 3.3 V as high and recommends a signal above 4 V for
reliable operation. Maestro TX outputs 5 V logic and must not directly drive
an ESP32 input.

Ready-made module recommendation: **Pololu #2595, Logic Level Shifter,
4-Channel, Bidirectional**. Pololu explicitly documents asynchronous TTL
serial support. Use two of its four channels, short internal wiring, and
qualify the proposed 115200-baud connection on the bench. An alternative is
**SparkFun BOB-12009, Logic Level Converter - Bi-Directional**, which supports
simultaneous 3.3 V-to-5 V and 5 V-to-3.3 V translation. Neither physical link
has been tested on Alice yet. These modules use MOSFETs and pull-up resistors;
they are not the same circuit as the earlier TXU0202 IC candidate.

For Pololu #2595, proposed connections are LV to XIAO 3.3 V, HV to the
regulated 5 V logic supply, L1 to XIAO D2/TX, H1 to Maestro RX, L2 to XIAO
D3/RX, and H2 to Maestro TX. The devices must share ground; this particular
module has no GND pin. HV is the 5 V logic reference, not the servo supply.
The board includes unsoldered header strip, or a pigtail can be soldered
directly to it. TXU0202 remains an option for a future custom carrier.

Removing the Maestro USB cable also removes its USB logic power. Mini Maestro
VIN accepts 5-16 V. The intended regulated 6 V servo supply can also supply
VIN through the documented `VSRV=VIN` jumper if the final power design uses
that arrangement. ReSpeaker/XIAO electronics need their separate regulated
5 V supply. Servo current must not pass through the XIAO or its GPIO wiring.
Maestro's 5 V output is not an adequate supply for the ReSpeaker/XIAO assembly.

## USB serial alongside UART

### Bench power clarification: 2026-09-16

The operator confirms the Maestro is currently powered by its USB cable.
For a verified dual-supply translator, its high-side reference can come from
the Maestro's documented **5V (out)** pin while the low-side reference comes
from XIAO 3V3; all logic grounds are common. This avoids needing a 5 V wire
from the ReSpeaker to the translator and keeps each reference associated
with its device's power domain. Do not join the two boards' USB-derived 5 V
outputs. The translator's actual supply pin identities remain unconfirmed.

The XIAO itself also exposes USB VBUS on its 5V pad. The official front
pinout, visually inspected, places this at the top of the right-hand pad
row with the component side facing the viewer and USB connector at the top;
GND and 3V3 are the next two pads below. See
[Seeed front pinout](https://files.seeedstudio.com/wiki/SeeedStudio-XIAO-ESP32S3/img/XIAO_ESP32-S3_front_pinout.png).
This physical description is for the XIAO, not the larger carrier's orientation.

Removing the Maestro USB cable still requires a separate logic-power path.
With a verified regulated 6 V servo supply, the documented VSRV=VIN jumper
can feed its VIN regulator, retaining 5V (out) for the translator. The 5V (out)
pin is not the documented external power input; VIN is. Never connect the
6 V servo rail directly to the translator's 5 V reference. No power connection
was changed or tested during this clarification.

The operator then clarified they want to supply Maestro **logic VIN** from
the XIAO's USB-derived 5 V. VIN is the correct external logic-power input;
Pololu specifies 5–16 V and about 50 mA controller consumption. Thus
XIAO 5V/VBUS -> Maestro VIN, with common ground, is a candidate only while
the measured VIN stays at or above 5 V under the combined USB load. A nominal
5 V USB source does not establish that condition after cable/connector drop;
Pololu explicitly does not guarantee operation below 5 V, even with Maestro
USB also attached. This source has not been measured under load.

For that separate logic-supply arrangement, leave **VSRV=VIN open** so the
separate servo supply is not connected to XIAO VBUS. Never tie the XIAO 5 V
output to Maestro 5V (out). Servo current stays on its own supply. The prior
6 V servo-to-VIN jumper proposal is an alternative and must not be combined
with an XIAO-to-VIN feed. No supply wiring or firmware was changed here.

The XIAO's native USB Serial/JTAG interface can expose a host serial port
while a separate hardware UART uses D2/D3. Bridge firmware could forward
commands from that USB serial port to the Maestro and return its replies;
Wi-Fi would be another input transport to the same controlled interface.
The current firmware emits diagnostic JSON and implements no such bridge.
USB/Wi-Fi control ownership and diagnostic output separation must be defined
before this can be used as a motor-command port.

The host would enumerate a XIAO serial device. Pololu Control Center's native
USB configuration interface would still use the Maestro's own USB connector.
In Maestro UART mode, its USB Command Port mirrors bytes received at RX,
but ignores bytes sent by the PC to that virtual serial port; it is therefore
not a second equivalent serial-command input. USB Dual Port mode has a
different purpose and does not make UART RX a Maestro command input.

## Software bring-up

1. Back up Maestro settings and configure UART mode with a fixed baud rate
   (proposed 115200, 8-N-1). USB remains useful for initial configuration.
2. Prove controller replies over the translated UART without motion.
3. Add a host/XIAO protocol with device selection, calibrated limits,
   malformed/stale-command rejection and defined network-loss behavior.
4. Configure and verify the Maestro serial timeout and channel default/error
   states. A timeout must not silently leave an unsuitable last pose active.
5. Add buffered audio and motion scheduling on the XIAO's clock for speaking.
   Observed Wi-Fi ping latency is variable, so it is not a timing reference.

The future motor adapter should remain dry-run by default. An explicit motion
test request would scope a later attended actuator trial.

## Sources and evidence

- [Seeed ReSpeaker Lite kit and schematic](https://wiki.seeedstudio.com/xiao_respeaker/)
- [Carrier schematic inspected](https://files.seeedstudio.com/wiki/SenseCAP/respeaker/respeaker_lite_v1.0_sch_1.png)
- [Carrier pinout at inspected commit](https://github.com/respeaker/ReSpeaker_Lite/blob/2ad81e22e773d1680793acdb3e14bb108d7566cb/doc/images/pinout.png)
- [XIAO GPIO mapping](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/)
- [Espressif configurable UART pins](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/serial.html)
- [GPIO3 and JTAG selection](https://docs.espressif.com/projects/esp-idf/en/v5.2/esp32s3/api-guides/jtag-debugging/configure-other-jtag.html)
- [Mini Maestro power and voltage thresholds](https://www.pololu.com/docs/0J40/1.b)
- [Maestro serial wiring](https://www.pololu.com/docs/0J40/7.c)
- [Maestro serial mode and timeout settings](https://www.pololu.com/docs/0J40/5.a)
- [TXU0202 specifications](https://www.ti.com/product/TXU0202)
- [Pololu #2595 module and UART support](https://www.pololu.com/product/2595)
- [SparkFun BOB-12009 module](https://www.sparkfun.com/sparkfun-logic-level-converter-bi-directional.html)
- [ESP32-S3 native USB serial interface](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/usb-serial-jtag-console.html)

The private diagnostic directory recorded in the
[XIAO inspection](../xiao-esp32s3-usb-evidence-2026-09-15.md) contains
`gpio3-jtag-readonly.log` and the subsequent application-reset log.
