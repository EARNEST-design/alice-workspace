# Face and speech: proposed USB-C parts list

Prepared: 2026-09-15 HKT.

Scope: external computer connected by one USB data cable; separate USB-C PD
charger powers the robot. Reuse the existing ReSpeaker Lite, Mini Maestro 12,
face servos, and compatible existing cables. Body motors and batteries are
outside this initial supply budget. This is a proposed bill of materials,
not a verified assembled system or a purchase record.

## Current cart reconciliation: 2026-09-16

This section reconciles the latest operator cart with the later Wi-Fi/UART,
2S battery and separate-charger/source-diode proposal below. The original
USB-hub bill of materials remains historical; it is not an additional order.

Operator-reported cart initially contained **one INA226, two INA219, one BMS,
and a BEC**. The operator subsequently confirmed the BMS is the **balanced**
version and reported adding the **DD23 charger and ideal-diode module(s)**.
Exact charger variant/current and diode quantity were not restated; the
required targets remain DD23CRTA 2S / 8.4 V / 0.5 A and two source diodes.
The BEC quantity is not explicit; the comparison assumes one. Exact module
models/shunts and selected BMS cutoff specifications remain unverified.
The operator subsequently confirmed **the order is placed** and reported
assembling the in-hand TTL level shifter. Exact delivered quantities and
variants remain unverified; no agent purchase action was performed.
Already in hand: USB-C 20 V module, TTL level shifter, Hongli cells,
ReSpeaker Lite with XIAO/antenna, and Maestro/face hardware.

Required quantities and remaining assembly items (charger and ideal diodes
are now reported added; do not duplicate them):

| Quantity | Item | Requirement |
| ---: | --- | --- |
| 1 | DD23CRTA 2S charger | Select 8.4 V / 0.5 A; manufacturer lists 10–23 V input for 2S. |
| 2 | Ideal-diode/source-combining modules | One external-source branch and one protected-battery branch; verify actual XL74610L board voltage/current/reverse-blocking specifications. |
| 1 additional | BEC/regulator | Two total: separate 6 V servo and 5 V electronics outputs, both covering the qualified 2S-to-20 V input range. A selection pad does not create two simultaneous outputs. |
| 1 set | Secure two-cell holder/harness | Series connection with accessible midpoint for the 2S BMS; two separate single-cell holders are an alternative. Check contact quality and retention. |
| 1 set | Battery and branch fuse holders/fuses, DC-rated switch | Reuse suitable existing hardware; final fuse values follow measured current and wire ratings. Fuses are not precision 900 mA current limiters. |
| 1 set | Power and signal connectors/wiring, headers, insulation and mounting | Include the XIAO-to-level-shifter-to-Maestro UART harness and sensor I2C wiring; use rated power connectors for motors. |
| If absent | USB-C PD wall supply and cable | Must advertise the 20 V profile and supply combined system and charger demand. The PD trigger alone is not the wall supply. |

The proposed three monitors cover servo, electronics and pack measurements.
Prefer a suitably rated INA226 module with R010 shunt for the servo branch;
the INA219 modules can serve electronics and the low-current pack branch if
their actual shunts/current paths cover the loads. Use distinct I2C addresses
and 3.3 V-compatible pull-ups. These are monitors, not autonomous current
limiters or battery disconnects.

No battery-operation acceptance is implied: the cited Hongli 900 mA discharge
limit may be insufficient for combined face/audio peaks. Battery overcurrent
protection/load limiting and the cutoff needed to preserve 6 V buck regulation
remain to be resolved from measurements and actual board specifications.
The BMS cutoff specifications remain pending. No purchase or hardware test
was performed in this cart review.

## Main parts (original USB-hub proposal)

| Quantity | Part | Function / selection |
| ---: | --- | --- |
| 1 | 65 W USB-C PD charger | Example: Anker 715 / Nano II 65 W, offering 20 V at 3.25 A. Choose the local mains-plug version. Reuse an equivalent existing charger if available. |
| 1 | USB-C to USB-C power cable | E-marked, 5 A, 100 W or higher rating; charger to PD input. |
| 1 | Adafruit HUSB238 switchable PD breakout #5991 | Requests 20 V; terminal output supplies the two regulators in parallel. |
| 1 | Pololu D42V55F6 #5573 | Fixed 6 V regulator for the Maestro servo power rail; 5.5 A nominal product class, subject to load and thermal qualification. |
| 1 | Pololu D42V55F5 #5571 | Fixed 5 V regulator for the powered USB hub. |
| 1 | Waveshare USB-HUB-4U | Four USB-A downstream ports, USB-B upstream, dedicated 5 V DC input. Manufacturer documents reverse-current protection and up to 1.2 A per downstream port. |
| 0 or 1 | Seeed Mono Enclosed Speaker, 4 ohm / 5 W, SKU 114993346 | Matched speaker option for ReSpeaker Lite; reuse an existing compatible speaker if present. |

## Wiring and mounting

| Quantity | Part | Notes |
| ---: | --- | --- |
| 1 | Short USB-A to USB-C data cable | Hub to the ReSpeaker's XMOS USB-C port. |
| 1 | Short USB-A to Mini-B data cable | Hub to the Mini Maestro; reuse its existing cable if suitable. |
| 1 | Host-to-USB-B data cable | USB-A or USB-C host end to suit the external computer. Check whether the hub package includes the required cable. |
| 1 | Hub DC power pigtail | Match the hub's actual barrel dimensions and polarity; connects only to the 5 V regulator. |
| 1 set | Power distribution terminals and insulated power connectors | Split PD output to the two regulator inputs; connect the 6 V output to the Maestro's servo power input. |
| As measured | Flexible stranded red/black power wire | Size to final branch current, length, voltage-drop limit, and terminal capacity. |
| 2 | Inline branch fuse holders and suitable fuses | One for the servo branch and one for the electronics branch; final fuse ratings depend on wiring and measured loads. |
| 0 or 1 | DC-rated motor power switch | Reuse the existing master motor switch if its rating and wiring are suitable. |
| 1 set | Mounting plate, standoffs, screws, heat-shrink, cable clamps | Secure and insulate boards, joints, and cable entries. |

Assembly tools: soldering iron for regulator connections, appropriate crimping
tools, and a multimeter. A current logger with adequate range/bandwidth is
useful for measuring motor supply peaks; an ordinary USB power meter alone
does not establish the 6 V rail's transient performance.

## Qualification still required

- The recorded 6 V / 1 A face supply is operator-reported, scoped evidence;
  face motor models and coordinated-motion current remain unverified.
- Both regulators' current ratings depend on operating conditions. Their
  maximum ratings are not simultaneously available from a 65 W charger.
- Check ReSpeaker playback current against the hub's 1.2 A per-port limit,
  particularly when using the speaker amplifier at high volume.
- Verify actual hub power connector and upstream backfeed behavior before
  connecting the assembled power system to the external computer.
- The hub is powered from 5 V, and the servo rail from 6 V. The negotiated
  20 V feeds only the regulator inputs.

## Future additions

- Wi-Fi, USB-based route: an onboard computer with Wi-Fi and USB host
  capability, storage, mounting, and a correctly rated supply connection.
  This offers more reuse of the current USB-based software.
- Wi-Fi, compact controller route: ReSpeaker Lite with a XIAO ESP32S3 and
  antenna. The bare ReSpeaker has no Wi-Fi; the XIAO provides 2.4 GHz Wi-Fi.
  The operator confirmed a XIAO ESP32S3 attached to the ReSpeaker Lite on
  2026-09-15. XIAO USB communication and stored-image integrity were checked;
  Wi-Fi joined successfully after the antenna was attached; audio operation
  remains unverified. A candidate design uses I2S
  audio between XIAO and ReSpeaker, and UART serial commands/status between
  XIAO and Maestro, with suitable 3.3 V/5 V signal conditioning. Pin allocation,
  Maestro logic power without USB, and serial settings still need design
  verification. Seeed's I2S example uses GPIO43/44, which are also the XIAO's
  default UART pins: the Maestro UART would need a verified alternative pin
  assignment. This could remove the USB hub for face/audio-only operation.
  It requires custom firmware and host integration for buffered audio,
  synchronized motion, calibrated limits, and fault/network-loss handling;
  the current Linux/Python/ROS software does not run unchanged on the XIAO.
  The external computer would still provide higher-level processing over
  Wi-Fi. This is a feasibility proposal, not an implemented capability.
  The operator attached the external antenna after the initial failed
  connection test; the retry joined the requested network successfully.
  See the [UART proposal](xiao-maestro-uart-proposal-2026-09-15.md) for carrier
  breakout pads, voltage translation in both directions, and remaining work.
  Add one **Pololu #2595 four-channel bidirectional logic level shifter**
  (alternative: **SparkFun BOB-12009**) and a short connector/pigtail harness
  for this UART route. Only two channels are needed. This addition is not
  required for the original USB-hub route; the physical UART link still needs
  bench qualification.
- Body motors: USB-to-Dynamixel interface such as U2D2, compatible power
  distribution, motor-specific cables, and a supply sized from actual motor
  models and loads. The operator now tentatively identifies the arm/leg
  motors as RX-28: if confirmed, these require RS-485 and 12–18.5 V, with
  14.8 V recommended. The Maestro level shifter is not an RS-485 interface.
  U2D2 supplies communications only; motor power is external, and legacy
  RX-28 connector compatibility needs checking. Reuse existing compatible
  hardware after inventory. Body work remains deferred.
- Face battery candidate: a USB-C PD power bank supporting the selected
  20 V profile and sufficient continuous/peak power could replace the wall
  charger while retaining the PD trigger and regulated 5 V/6 V supplies.
  Actual capacity, discharge behavior, idle shutoff, and runtime remain
  unverified. A battery alone does not replace the USB data connection.
  No specific bank is selected. A raw battery pack would instead require
  its own protection, charging, and power-path design.
- Available raw cells: the operator reports approximately ten 3.7 V /
  1800 mAh cells, intended for face/audio only. Their model, discharge rating,
  condition and protection are unknown. A single-cell supply would need
  suitable boost converters for the 5 V and 6 V rails; the D42V55F5/F6
  step-down boards cannot raise 3.7 V to these outputs. A series-pack design
  needs its own charger and protection/balancing, and enough input voltage
  above 6 V throughout the usable discharge range for regulator dropout.
  Do not charge a series pack through the XIAO's single-cell battery input.
  The operator subsequently proposed **two cells in series (2S)** as the
  preferred candidate: assuming standard 4.2 V-charge Li-ion/LiPo cells,
  this is 7.4 V nominal / 8.4 V full, still 1800 mAh. Supply the two regulators
  from the protected pack output. Include 2S protection/balancing, a suitable
  8.4 V CC/CV charger, fuse and switch. Qualify a cutoff that preserves the
  6 V buck regulator's headroom under servo peaks; a buck-boost converter is
  an alternative for maintaining 6 V at lower pack voltage. Exact cells,
  component ratings, cutoff and runtime remain unverified.

## Procurement update: 2026-09-16

Operator reports a USB-C 20 V port/module and TTL level shifter physically in
hand. Their exact models and measured behavior have not been supplied; do not
equate them with the example HUSB238 and Pololu boards above. Confirm that the
USB-C module negotiates PD, rather than assuming a connector supplies 20 V.
The operator is considering three INA219 I2C boards, three XL74610L boards and
a 2–6S, 5 A, 5 V BEC. These remain proposed orders, not tested components.

### Charger candidates for the proposed 2S pack

For standard 4.2 V-charge cells, select an assembled charger configured for
8.4 V CC/CV, with an input rating covering the negotiated 20 V and a charge
current matched to the identified cells. Capacity alone does not establish
the permitted charge current. Candidate chip names and Taobao search terms:

- **CN3762**: `CN3762 2串 8.4V 锂电池 充电模块`.
  The manufacturer's IC specification is 6.6–30 V input and 8.4 V charge
  regulation. An actual module must separately be rated for 20 V input and
  configured for the appropriate current. Treat a plain charger module as
  charging with the load switched off unless a separate power path is designed.
- **BQ24610**: `BQ24610 2S 8.4V 充电模块 电源路径`.
  TI specifies 5–28 V input and optional adapter/battery power selection.
  For operation while charging, require the actual board to implement that
  power path with separate system/load and battery connections; the chip name
  alone is insufficient because a non-power-path circuit is also supported.
  In the standard adapter-selection topology, the system supply can be near
  20 V on external power and near pack voltage on battery. Both downstream
  regulators must tolerate this entire range.
- **TP5100** is not a direct-20-V candidate. Top Power's product catalog
  advertises 5–18 V input, but its REV_2.4 English datasheet specifies up to
  12 V operating input and 18 V absolute maximum. Do not design around the
  catalog's higher figure; verify the actual module and use a lower input.

These are search terms based on manufacturer specifications, not verified
Taobao listings. A 2S protection/balancing board is still a separate pack
requirement unless explicitly implemented in the chosen assembly. Search
`2串 7.4V 8.4V 锂电池 保护板 带均衡`; current ratings remain load-dependent.
The charger is not replaced by a protection board, ordinary buck converter,
or ideal diode. The XIAO single-cell charger remains unsuitable for 2S.

### Other proposed parts

- **INA219:** its maximum sensed bus voltage is 26 V. With a 0.1-ohm shunt
  marked R100, the maximum configured shunt range of 320 mV corresponds to
  3.2 A. This is not enough to measure a 5 A branch without saturation;
  resistor dissipation and PCB/terminal ratings also constrain use.
  Consider **INA226 with a 0.01-ohm R010 shunt**: 36 V bus range and an
  approximately 8.19 A theoretical measurement span from 81.92 mV / 0.01 ohm.
  That span is not a board current rating. Select current paths for actual
  peaks. Three monitors can share I2C using distinct addresses and 3.3 V
  logic/pull-ups, avoiding the ReSpeaker's existing device addresses.
- **XL74610L:** the exact listing/schematic has been requested; manufacturer
  specifications have not been established. If these are the proposed
  ideal-diode modules, their role is reverse-current blocking/source combining,
  not voltage regulation or battery charging. Quantity and placement remain
  undecided until the charger and power path are selected.
- **2–6S, 5 A, 5 V BEC:** candidate for the electronics/audio rail, subject to
  exact input limits, continuous-current/thermal data and noise qualification.
  The separate 6 V servo regulator is still required. Servo peak load and 2S
  buck dropout remain unresolved as described above.

### Follow-up: separate charger and two ideal diodes

The operator could not find a charger module with power-path management and
proposes a regular charger plus two ideal diodes. They also report that the
BEC has a solder pad for selecting 6 V. This supports the following candidate
arrangement; exact board specifications remain unverified:

```text
USB-C PD 20 V ---+--> ideal diode 1 ---+--> system bus --> BEC at 6 V --> servos
                |                    |              +-> BEC at 5 V --> audio/ESP
                +--> 2S charger       |
                         |           |
                         v           |
                 protected 2S pack --> ideal diode 2 -+
```

This is a functional block diagram, not a terminal wiring drawing. Both ideal
diodes conduct toward the system bus. The charger input connects to the USB-C
source before the first diode, not to the combined system bus. Its output
connects through the BMS's prescribed charging terminals, on the battery side
of diode 2. Charging must not bypass the battery diode from the system bus.
Respect BMS charge/discharge terminal assignments, including negative
terminals; do not bypass low-side protection by tying cell B- to system ground.
Fuse and switch placement and ratings still follow the final wiring/load.

With established 20 V PD power and sufficient source capacity, the higher
voltage source supplies the system and diode 2 blocks current into the pack.
The separate charger then measures battery charging current without normal
robot load current. When external power is removed, the battery supplies the
bus through diode 2. Both BECs must tolerate the entire pack-to-20-V input
range and the transition between sources. This design does not provide
automatic adapter power budgeting: the adapter/PD module/cable must support
robot input power plus charging power and losses. Switchover and servo peaks
require a loaded bench check; uninterrupted operation is not yet demonstrated.

The charger module must independently prevent battery-to-input backfeed when
USB-C is absent. Otherwise that route bypasses diode 2 and can feed the USB-C
source node; an additional appropriately placed blocking device or a different
charger module is required. The two OR-ing diodes alone do not establish this.
Do not infer XL74610L ratings or behavior from TI's LM74610 name similarity.

Assuming the solder pad selects the single BEC output, use two BEC modules:
one at 6 V and one at 5 V. The pad does not imply simultaneous outputs. Check
6 V regulation at low 2S voltage under load and continuous/peak current at both
input extremes. Keep the existing BMS/balancing and cell-current requirements.

The exact XL74610L, BEC and chosen charger listings/schematics remain needed
for a terminal-level design. No circuit was assembled, powered, or accepted
from these part names alone.

### Follow-up: HX-2S-JH20 protection-board candidate

The operator found a board advertised as **HX-2S-JH20, 2S, 10 A, 7.4 V**.
This is a protection-board candidate, not the missing charger. Sunhokey's
listing identifies shared P+/P- charge/discharge connections, B+/B- pack
connections and an MB cell-midpoint connection, and advertises 8.4–9 V charging
input. For the proposed standard 4.2 V-per-cell pack, use a dedicated **8.4 V
CC/CV 2S lithium charger**; do not interpret the listing's 9 V upper number or
overcharge trip threshold as the desired charging voltage. Do not apply 20 V
to this BMS or to the cells. Exact unit layout, balancing implementation,
thresholds and continuous-current capability still require verification.

The charger takes the 20 V input and performs the regulated charging step.
A suitable CN3762 module can do this without an intervening BEC, subject to
its actual board ratings, charge-current configuration and reverse blocking.
An ordinary BEC set to 8.4 V does not establish controlled lithium charging:
the circuit needs appropriate current regulation, voltage accuracy and charge
termination. The BMS protection cutoff is not normal charge regulation. Its
advertised 10 A rating does not set or authorize 10 A charging of the cells.

### Follow-up: FDC-2S-22S inquiry

The operator subsequently asked about **FDC-2S-22S**. Search results identify
this name with small 2S protection boards advertised at 3 A continuous / 5 A
transient, sometimes misleadingly titled "charger". No manufacturer schematic
or regulated-charger specification was established for the operator's exact
board. Treat it as an unverified protection-board candidate, not a replacement
for the 20 V-input, 8.4 V CC/CV charger. Do not apply 20 V to it. Its advertised
3 A path also cannot establish adequate capacity for Alice's unmeasured
face/audio battery peaks. No order, wiring or power test occurred.

Discovery listing (seller claims, not qualification evidence):
[FDC-2S-22S listing](https://smarthallroad.com/product/fdc-2s-22s-3a-7-4v-8-4v-18650-lithium-lipo-cell-battery-charger-board-li-ion-battery-charging-pcb-bms-protection-module-hx-2s-01-in-pakistan).

### Alternative sourcing route: 5 V-input boost charger

The operator reports difficulty finding a suitable charger and observes that
many available modules charge 2S by boosting 5 V. This is a viable alternative
to a direct-20-V charger; it is proposed here, not yet selected or assembled:

`USB-C 20 V -> dedicated 5 V buck/BEC -> 5 V-input 2S charger -> BMS -> cells`.

Feed this additional charging-only buck from the USB-C source before the
OR-ing diodes, so it loses input power when USB-C is removed. Do not reuse the
battery-backed 5 V electronics rail as charger input: that would create a
battery-to-converter-to-charger-to-battery loop. Keep the two source-selection
diodes and separate 5 V/6 V load regulators from the preceding proposal.
The actual charger must still block battery backfeed through its input, and
charging/adapter current must be sized to the identified cells and total load.

Candidate assembled-module search terms:

- `IP2326 2S 8.4V 5V输入 平衡充电模块`: the published IP2326 datasheet
  describes boost charging with 5 V input and configurable 2S charge voltage.
  Select an 8.4 V variant with appropriate adjustable charge current; do not
  assume another cell-count/voltage variant is interchangeable. An integrated
  balancing function does not establish pack discharge protection. The exact
  module's midpoint/BMS wiring still needs review.
- `TP5200 5V 8.4V 双节锂电池 充电模块`: Top Power's REV_1.1 datasheet
  specifies 4.5–6.5 V operating input, 8.4 V charging and 0.1–1 A programmable
  charge current with termination. This is a different device from the
  previously discussed step-down TP5100. The datasheet's 20 V absolute maximum
  is not permission to operate it from the 20 V PD output.

Neither an actual stocked Taobao module nor its charge-current setting has
been qualified. This adds one conversion stage and one dedicated converter
to broaden sourcing choices; no battery charging trial occurred.

### Follow-up: TP5100 candidate with reduced input voltage

The operator asked specifically about TP5100. This is a genuine lithium
charging-controller candidate with precharge, CC/CV and charge termination,
unlike the preceding protection-only candidates. A suitable assembled board
can serve the 2S pack when configured for 8.4 V and a cell-appropriate charge
current. The precise board, selector arrangement and default current are not
yet known; do not assume the advertised 2 A maximum is suitable for the cells.

Proposed charging branch:
`USB-C 20 V -> dedicated adjustable buck set to 10 V -> TP5100 in 2S mode -> BMS -> cells`.

The 10 V candidate input leaves margin below the REV_2.4 datasheet's 12 V
operating ceiling while exceeding the 8.4 V charge voltage. Qualify regulation
and transients on the actual buck/module. The buck input remains connected
directly to USB-C upstream of the source-selection diodes, so it is not powered
by the battery. The existing 5 V/6 V BEC outputs cannot substitute for this
input: TP5100 steps down and does not boost 5 V to charge a 2S pack. Keep pack
protection/balancing, the two source diodes and separate load regulators.

Manufacturer documentation differs: the Chinese product page advertises
5–18 V operation, while the English REV_2.4 datasheet gives up to 12 V
operating input and 18 V absolute maximum. This corrects any inference that
18 V was a qualified operating voltage in the earlier notes. Neither source
permits direct 20 V operation. No firmware or hardware was changed or tested.

### Follow-up: DD23CRTA direct-20-V charger candidate

The operator asked about **DD23CRTA**. Eletechsup's own product listing and
downloadable DD23CRTA manual specify **10–23 V input, 8.4 V charging, and
0.5 A or 1 A options for the 2S version**. This is a published-specification
match for the existing 20 V PD source and removes the extra input buck required
by the TP5100/5 V boost alternatives. Treat it as a candidate, not an assembled
or measured system. Select the actual 2S/8.4 V variant; the listing's generic
"2S 2A / 5–23 V" title conflicts with its detailed 2S specification and SKU
`DD23CRTA_2S1A`. Do not infer 2 A charging or 5 V input capability for 2S.
The 0.5 A option reduces charge rate and board dissipation compared with 1 A;
neither setting is qualified for the unidentified cells from capacity alone.
The operator later clarified the label as **IMR18650, 1800 mAh, 6.66 Wh**.
The operator subsequently confirmed **Hongli**. The matching published model
specifies a 900 mA maximum charge current, so select the 0.5 A option over
the fixed 1 A version for cells covered by that specification. Series wiring
does not halve the current through either cell. See the
[cell-label clarification](body-actuator-inventory-2026-09-15.md#cell-label-clarification-2026-09-16)
for source provenance and outstanding condition/revision checks.

Charging branch:
`USB-C PD 20 V -> DD23CRTA in 2S/8.4 V configuration -> BMS -> cells`.

Keep the two source-selection diodes and separate 5 V/6 V load regulators.
Connect the charger input directly to the USB-C source before its source diode;
the charger output connects to the BMS-prescribed charge terminals. Keep pack
protection/balancing. A charger current of 0.5 A or 1 A does not limit the
separate robot power branch to that current. Total adapter power, actual board
heat/voltage accuracy, pack suitability and switching remain untested.

The manufacturer download archive contains `DD23CRTA.docx`,
`AN_SY6912A.pdf` and `EVB_SY6912A.pdf`. The manual repeats the module's
10–23 V / 8.4 V / 0.5 A-or-1 A 2S specifications. The bundled preliminary
SY6912A datasheet describes trickle/CC/CV charging, charge completion, and
automatic shutdown preventing reverse energy flow. This supports the candidate
charging design but does not physically identify or verify the supplied board.
Exact module reverse-blocking behavior remains a bench check.

Documentation conflict for later bench work: the module manual describes a
flashing LED as "almost full", whereas the bundled IC datasheet identifies
flashing as fault mode. Do not diagnose charge completion from flashing alone;
resolve the actual board implementation and measure its behavior.

Read-only archive inspection was performed in
`/tmp/alice-dd23crta-49b9p01d/` using system libarchive and pdftotext. No
battery, regulator, servo or firmware was accessed. No purchase was made.

Sources for this update:

- [Consonance CN3762](https://www.consonance-elec.com/75.html)
- [TI BQ24610](https://www.ti.com/product/BQ24610)
- [Top Power charger catalog / TP5100](https://www.toppwr.com/product/?sortid=293)
- [TI INA219](https://www.ti.com/product/INA219)
- [TI INA226 datasheet](https://www.ti.com/lit/ds/symlink/ina226.pdf)
- [TI ideal-diode OR-ing application note](https://www.ti.com/lit/pdf/SNVA747)
- [Sunhokey HX-2S-JH20 listing](https://sunhokey.cn/products/2s-10a-8-4v-18650-lithium-protection-board)
- [IP2326 V1.2 datasheet](https://www.chipsourcetek.com/DataSheet/IP2326_V1.2.pdf)
- [Top Power TP5200 REV_1.1 datasheet](https://www.toppwr.com/uploadfile/file/20251201/692d51a48b3c6.pdf)
- [Top Power TP5100 REV_2.4 datasheet](https://www.toppwr.com/uploadfile/file/20240913/66e3a293b3c42.pdf)
- [Eletechsup DD23CRTA 2S specifications](https://www.485io.com/battery-charge-board-c-1_16/dd23crta-2s-2a-dc-523v-to-84v-2s-multicell-liion-lithium-battery-charger-for-solar-charging-portable-device-p-1453.html)
- [Eletechsup DD23CRTA manual and IC datasheet archive](https://485io.com/eletechsup/DD23CRTA.rar)

## DIYTZT balancing BMS candidate: 2026-09-16

The operator supplied a DIYTZT listing title offering multiple 2S current
ratings and Standard / Balance variants, plus a product image. The supplied
[seller image](https://ae-pic-a1.aliexpress-media.com/kf/Sca1d4f7439cd4b27ae6bf624d3c43957i.jpg_960x960q75.jpg_.avif)
was visually inspected in the browser. It explicitly shows **2S 20A Balance**,
board marking **HW-391**, dimensions 48.4 x 20.1 mm, two resistors marked
430, and pads labelled 0V, 4.2V, 8.4V, + and −. This identifies the pictured
variant, not the variant selected in any cart or a physically received board.

This is the requested category of 2S protection board with advertised
balancing, and remains a candidate alongside the 8.4 V / 0.5 A DD23CRTA
charger. The 20 A marking is an advertised board rating, not a charging
setpoint or evidence that the Hongli cells can provide that current. Do not
rely on this board to enforce the cited 900 mA cell discharge limit; pack
load/current protection still needs qualification.

Exact protection thresholds/tolerances, balancing behavior, connection
instructions and supplied component identity are not established by the
photo. Other sellers' HW-391 specifications were not adopted as evidence
for this specific DIYTZT variant. A matching-title AliExpress listing could
not be inspected: web retrieval failed and browser site-safety policy
explicitly blocked opening the product page. No workaround was attempted.
The operator was asked to paste this variant's overcharge and over-discharge
cutoff specifications. No board was purchased, wired or tested.

## Manufacturer sources checked

- [Anker 715 specifications](https://www.anker.com/products/a2663114-1-a8552012-1)
- [Adafruit HUSB238 #5991](https://www.adafruit.com/product/5991)
- [Pololu 6 V regulator #5573](https://www.pololu.com/product/5573)
- [Pololu 5 V regulator #5571](https://www.pololu.com/product/5571)
- [Pololu UART level-shifter module #2595](https://www.pololu.com/product/2595)
- [SparkFun alternative BOB-12009](https://www.sparkfun.com/sparkfun-logic-level-converter-bi-directional.html)
- [Waveshare USB-HUB-4U specifications](https://www.waveshare.com/wiki/USB-HUB-4U)
- [Seeed speaker](https://www.seeedstudio.com/Mono-Enclosed-Speaker-4R-5W-p-5931.html)
- [Mini Maestro 12 cable and connector information](https://www.pololu.com/product/1352)
- [ROBOTIS U2D2](https://emanual.robotis.com/docs/en/parts/interface/u2d2/)
- [ROBOTIS RX-28 specifications](https://emanual.robotis.com/docs/en/dxl/rx/rx-28/)
- [ROBOTIS U2D2 Power Hub](https://emanual.robotis.com/docs/en/parts/interface/u2d2_power_hub/)
- [ReSpeaker Lite with XIAO and I2S firmware](https://wiki.seeedstudio.com/xiao_respeaker/)
- [XIAO ESP32S3 Wi-Fi specifications](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/)
- [Seeed I2S pin assignment example](https://wiki.seeedstudio.com/respeaker_volume/)
- [Maestro microcontroller connection](https://www.pololu.com/docs/0J40/7.c)
- [Maestro serial voltage levels](https://www.pololu.com/docs/0J40/5.b)

See also the [tentative body inventory](body-actuator-inventory-2026-09-15.md).
The [XIAO inspection record](../xiao-esp32s3-usb-evidence-2026-09-15.md)
contains the connected unit's firmware findings and private backup location.
