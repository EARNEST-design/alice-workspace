# XIAO ESP32-S3 USB connection evidence

## Initial USB enumeration

Observed: 2026-09-15 HKT, following the operator's report that they plugged in
a XIAO ESP32-S3. Read-only `lsusb`, `/dev/serial/` listings, and Linux USB sysfs
inspection found:

- Manufacturer: Espressif.
- Product: USB JTAG/serial debug unit.
- USB VID:PID: `303a:1001`.
- USB serial: `E0:72:A1:FB:E5:DC`.
- Current USB topology path: `6-1` (bus 006, device 002).
- Current serial node: `/dev/ttyACM2` (may change on reconnect).
- Stable serial link:
  `/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_E0:72:A1:FB:E5:DC-if00`.

The observed Espressif device is consistent with the operator-reported XIAO.
The generic USB descriptor alone does not independently identify the exact
board model. Attachment to the ReSpeaker carrier is not established by this
USB observation. No separate ReSpeaker/XMOS USB audio device appeared in the
USB inventory captured for this check.

During this initial enumeration, no serial port was opened, firmware read or flashed, reset requested, Wi-Fi
configuration inspected, or actuator command sent. This evidence establishes
USB enumeration and serial-node creation, not firmware or wireless readiness.

## Inspection of the original firmware

The operator subsequently confirmed that the XIAO is attached to the larger
ReSpeaker Lite board and requested firmware checks and Wi-Fi setup. Carrier
attachment is operator-reported; it is not inferred from the USB descriptor.

### Scope and method

- Verified the XIAO's VID, PID and serial before opening its stable serial path.
- Granted a temporary, device-specific ACL to `alice` on `/dev/ttyACM2` because
  the account is not in `dialout`. No persistent udev or group change was made.
- Captured serial boot output, read chip/flash/security information, and read
  all 8 MiB of flash using `esptool 5.1.0` in an isolated temporary environment.
- These operations restarted the XIAO and ran esptool's temporary RAM stub.
  No persistent firmware, flash settings or eFuses were written. The original
  application was restarted after inspection.
- Did not open the Maestro's ports, send actuator commands, or update XMOS.

### Findings

| Check | Observed result | Limit of evidence |
| --- | --- | --- |
| MCU | ESP32-S3 QFN56 revision v0.2; 40 MHz crystal | Identification, not a full functional test |
| Flash | 8 MiB, manufacturer `c8`, device `4017` | Full read completed successfully |
| PSRAM | Chip identification reports embedded 8 MiB AP_3v3 | No PSRAM memory test performed |
| Security state | Secure Boot disabled; Flash Encryption disabled | Read-only status query |
| Application | Valid ESP32-S3 image in `app0`, 262,128 bytes including its validation hash | Exact sketch and purpose unidentified |
| Integrity | Image checksum and validation hash both valid | Integrity does not establish application correctness |
| Arduino target | Embedded build strings identify `XIAO_ESP32S3` and Arduino ESP32 core `3.0.0-alpha3` | Not a recognized Alice or ReSpeaker application version |
| ESP-IDF metadata | `v5.1.2-185-g3662303f31-dirty`; project `arduino-lib-builder`; version `e2f746c` | May identify the prebuilt Arduino core rather than the sketch |
| Compile metadata | `Nov 29 2023 21:32:24` | Not a proven date for the installed sketch |
| Serial output | ROM/bootloader load messages; no application status or provisioning banner captured | An undocumented interface could still exist |
| Wi-Fi settings | Entire NVS partition at `0x9000`, length `0x5000`, is erased (`0xff`) | Does not exclude credentials compiled into an unidentified application |
| Network availability | Host Wi-Fi scan sees `EARNESTdesign` at 2412 MHz with WPA2; a 5300 MHz BSSID also exists | Host scan only; XIAO association has not been tested |
| ReSpeaker audio | No separate XMOS USB audio/DFU device appeared; ALSA has no ReSpeaker device | XIAO USB enumeration does not establish XMOS firmware or I2S operation |

The image contains no recognized ESPHome, MicroPython, CircuitPython or
ReSpeaker application markers in the inspected strings. Absence of those
strings is not sufficient to identify the sketch or prove missing capabilities.

Compared the backup against Seeed's published `XIAO-ESP32S3-firmware-20240814.zip`
without executing its contents. The application, bootloader and partition
images do not match. Only the standard `boot_app0.bin` matches at `0xe000`.
Therefore the installed application is not identified as that factory release.

### Partition layout

| Label | Offset | Size |
| --- | --- | --- |
| nvs | `0x9000` | `0x5000` |
| otadata | `0xe000` | `0x2000` |
| app0 | `0x10000` | `0x330000` |
| app1 | `0x340000` | `0x330000` |
| spiffs | `0x670000` | `0x180000` |
| coredump | `0x7f0000` | `0x10000` |

`app1` is erased. The first 128 KiB of the full backup matches the earlier
independent prefix read.

### Private artifacts and credentials

Original full-flash backup, outside the repository, mode `0600`:

`/home/alice/.local/state/alice-hardware/xiao-e072a1fbe5dc/original-flash-20260915.bin`

- Size: 8,388,608 bytes.
- SHA-256: `afe7b55ef757e56434c6f20add6db9604ffe951c1e24742cb6def5248b6c6362`.
- Keep private: a firmware backup can contain application data and credentials.
- Restoration has not been tested by writing the backup back to the device.

The operator saved Wi-Fi credentials in
`/home/alice/.config/alice-hardware/xiao-wifi.env`, mode `0600`, outside the
repository. The SSID matches the requested network and the password is present.
The password was neither displayed nor copied into repository files. Parse
values literally after the first `=`; do not source this file as shell code.

### Status after inspection, before provisioning

USB communication, flash reading and stored-image integrity are verified.
Wi-Fi connection, XMOS firmware version, microphone capture, speaker playback,
I2S operation and battery operation remain unverified.

No provisioning interface was identified in the existing XIAO application.
The proposed next step was a minimal Alice diagnostic application: provision
Wi-Fi from the private file, report connection/IP and reconnect behavior over
USB serial, and attempt a read-only XMOS version query through the documented
I2C interface. It would replace the current XIAO application. It would contain
no actuator control, audio streaming, network command service or automatic XMOS
firmware update. Its subsequent installation and results are recorded below.

The XMOS I2C interface is documented for I2S firmware. Failure to answer an
I2C version query alone would not prove a hardware fault. Connecting the
separate XMOS USB port is another route to firmware identification.

### Sources

- [Seeed XIAO ESP32S3 specifications and factory firmware](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/)
- [Seeed factory firmware archive used for comparison](https://files.seeedstudio.com/wiki/SeeedStudio-XIAO-ESP32S3/res/XIAO-ESP32S3-firmware-20240814.zip)
- [ReSpeaker Lite with XIAO](https://wiki.seeedstudio.com/xiao_respeaker/)
- [ReSpeaker Lite firmware and I2C interface](https://github.com/respeaker/ReSpeaker_Lite/)
- [Espressif esptool basic commands](https://docs.espressif.com/projects/esptool/en/latest/esp32s3/esptool/basic-commands.html)

## Wi-Fi diagnostic installation and initial results

Following the operator's request to set up Wi-Fi and their saved credentials,
installed a throwaway diagnostic application on the identified XIAO. This is
the currently installed application, replacing the unidentified Arduino sketch
described above. The original full-flash backup remains intact and private.

### Build and write evidence

- Application identity: `alice-wifi-diagnostic-1`.
- Arduino CLI `1.5.1`; Espressif Arduino core `3.3.11`, ESP-IDF `v5.5.5`.
- Board: `XIAO_ESP32S3`, hardware USB CDC/JTAG, CDC enabled, OPI PSRAM,
  QIO flash configuration, 8 MiB flash, default 8 MiB partition scheme.
- Custom `partitions.csv` preserves the observed layout. The generated
  partition binary exactly matched the original device's partition binary.
- Build passed. Initial image checksum and validation hash passed.
- Wrote bootloader at `0x0`, partition table at `0x8000`, OTA selection at
  `0xe000` and application at `0x10000`. esptool verified each written range.
- Added scan/disconnect telemetry after the first connection failure, rebuilt
  successfully, and rewrote only the application. Its write hash was verified.
- Current application image: 871,840 bytes, SHA-256
  `af1cd3e6f9013144c4d23f2d0587ad0c87b392a0d1512147c74a4e67730e00fa`.
- The first acceptance check failed against the original firmware because it
  did not expose the diagnostic interface. Subsequent checks received the new
  diagnostic reports but failed because Wi-Fi had not connected.

The diagnostic starts Wi-Fi without waiting for USB, retries disconnected
connections, and reports device identity, IP, RSSI and failure details over
serial. It sends only Seeed's documented XMOS firmware-version read request
(`0x42`, resource `0xF0`, command `0xD8`) on GPIO5/6. It does not operate motors,
start audio capture/playback, open a network command service or update XMOS.

### Observed results without an antenna

The operator explicitly reported that no external antenna is connected and
that the access point is nearby.

| Check | Result |
| --- | --- |
| Diagnostic boot and USB reports | Working; correct MAC `E0:72:A1:FB:E5:DC` |
| Flash / initialized PSRAM reported by application | 8,388,608 bytes each; no memory stress test |
| XMOS query | Success status `0`; firmware **1.0.8** |
| XIAO's own Wi-Fi scan | 13 networks found, one matching requested SSID |
| Requested network signal at boot | **-81 dBm** |
| Wi-Fi mode initialization | Successful |
| Wi-Fi connection | Not established; status `6`, IP `0.0.0.0` |
| Disconnect reasons | `2` (`WIFI_REASON_AUTH_EXPIRE`) and `4` (`WIFI_REASON_ASSOC_EXPIRE`) |

These results establish that the radio detects the network, but not that it
can sustain association. Weak reception without the antenna is a plausible
cause; the error codes alone do not establish an incorrect password. The
operator has been asked to attach the supplied antenna before the next retry.
LAN reachability and reset/rejoin testing depend on a successful connection.
Microphone capture, speaker playback and battery operation remain untested.

### Reproducibility and private artifacts

Diagnostic directory:

`/home/alice/.local/state/alice-hardware/xiao-e072a1fbe5dc/wifi-diagnostic-20260915/`

It contains `scope.md`, `manifest.json`, the sketch, the opt-in serial
acceptance check, build configuration, write logs and captured JSON reports.
The manifest records source provenance and artifact hashes. The private build
environment selects only ESP32-S3 dependencies from the Espressif index;
upstream archive URLs/checksums and platform source were preserved, and the
original index was retained. This does not change the repository's toolchain.

**Credentials are embedded in the diagnostic application.** The generated
header, binaries and build cache must remain private, along with the `.env`
file and original backup. The containing diagnostic directory is mode `0700`.
No password or credential-bearing binary was copied into the repository.

Protocol provenance:
[Seeed read-register example at inspected commit](https://github.com/respeaker/ReSpeaker_Lite/blob/2ad81e22e773d1680793acdb3e14bb108d7566cb/xiao_esp32s3_arduino_examples/xiao_i2c_get_register_value/xiao_i2c_get_register_value.ino).

## Antenna attached: Wi-Fi connection verified

The operator then attached the antenna. With the same credentials and
firmware, the XIAO joined `EARNESTdesign`, channel 1, with IP
`192.168.188.63`. Reported connected RSSI was **-43 dBm**, compared with
the earlier antenna-free scan's -81 dBm. The serial acceptance check passed.
An explicit reset followed by the same acceptance check also passed; a
connected report arrived at approximately 6.8 seconds of firmware uptime.
This is a reset/rejoin check, not an independent battery or cold-power test.

Host-to-XIAO ping passed with **4/4 replies** when explicitly bound to
Ethernet interface `enp2s0`, and passed again after reset/rejoin. The second
sample's round-trip range was about 108-275 ms; this short observation does
not qualify real-time streaming performance.

The default host route to `192.168.188.63` uses `tailscale0`, table 52,
despite the local Ethernet interface being `192.168.188.79/24`. An unbound
ping therefore failed. No host route or Tailscale settings were changed.
Host-side Alice integration must resolve this route selection or explicitly
use the local interface; association success alone does not establish that
the existing application can reach the XIAO using its default sockets.

Private evidence: `with-antenna.json`, `rejoined.json`,
`rejoin-reset.log`, `ping-with-antenna.log` (unbound failure), and
`ping-ethernet-rejoined.log` (explicit-interface success), alongside the
updated manifest. XMOS firmware remains 1.0.8. Audio capture/playback,
Maestro UART control and battery operation remain untested.

The subsequent read-only GPIO3/JTAG check for the
[Maestro UART proposal](electrical/xiao-maestro-uart-proposal-2026-09-15.md)
did not change eFuses or firmware; the diagnostic application was restarted.
