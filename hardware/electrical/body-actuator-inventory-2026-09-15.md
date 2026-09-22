# Tentative body actuator inventory

Recorded: 2026-09-15 HKT.

## Provenance and confidence

Source: the operator's description in the ReSpeaker/USB-C power planning
conversation. The operator expressed uncertainty about the layout and hand
motor type. This is a planning estimate, not a physically verified inventory.
No motor labels, wiring, bus identities, voltage ratings, or load currents were
verified during this discussion.

## Estimated layout

Assumptions: left and right limbs have matching layouts, and each listed
movement uses one actuator.

| Group | Movements per side | Actuators per side | Both sides |
| --- | --- | ---: | ---: |
| Arms, excluding hands | Shoulder rotation; shoulder elevation; elbow spin; elbow bend | 4 | 8 |
| Hands | Clasp; operator suspects a different motor type | 1 | 2 |
| Legs | Hip: 2 axes; knee: 1 axis; ankle: 2 axes | 5 | 10 |
| Total | Excludes face and neck/head actuators | 10 | 20 |

Derived estimate: 18 main joint actuators plus 2 hand-clasp actuators.
The operator reports multiple Dynamixels in the body, but which joints use
Dynamixels, and whether all main joints do, remain unknown.

## Other operator-reported hardware and intended scope

- The body contains an old Atom PC and its connected power supply. The operator
  would like to remove these to free space; other loads served by that supply
  have not been traced.
- There is space/wiring for batteries in the feet. Battery specifications,
  existing packs, wiring ratings, and protection circuitry are unknown.
- Near-term scope is face motion and speech, with USB or Wi-Fi connectivity.
  Whole-body motion and battery operation are future work.
- The existing face supply was previously reported as 6 V, 1 A; see
  [the scoped supply evidence](alice-servo-supply-6v-1a.md). That record does
  not establish simultaneous multi-servo capacity or body motor voltage.

## Inputs still needed for power sizing

- Motor model and quantity for each distinct type, including hand actuators.
- Rated voltage, current requirements, and communication interface per type.
- Existing controller and power-distribution wiring, connector ratings, and
  whether the old PC supply also serves motors or other retained hardware.
- Measured face current during representative coordinated motion, including
  peaks, before accepting a replacement face supply's current capacity.

The proposed USB-C face supply is a candidate architecture only. The body
actuator count does not establish that it can power whole-body motion.

## Follow-up: tentative RX-28 identification and bus planning

The operator subsequently described the arm/leg motors as "rx-28 i think old
version". Treat RX-28 as a candidate model, not a verified identity for any
particular joint. Hand actuator identity remains unknown.

For a confirmed RX-28, the [ROBOTIS manual](https://emanual.robotis.com/docs/en/dxl/rx/rx-28/)
specifies 12–18.5 V input (14.8 V recommended), half-duplex RS-485, and a
four-pin connection: GND, VDD, DATA+, DATA−. It uses Dynamixel Protocol 1.0.
The published 1.9 A stall figure is at 18.5 V; it is not a measured operating
current for Alice and does not establish a whole-body supply size.

Proposed architecture, not an accepted implementation:

- Keep the ReSpeaker XIAO responsible for face/audio. Its convenient spare
  carrier pads are already proposed for the Maestro UART; extra hardware
  UART peripherals do not by themselves provide accessible free pins.
- Use a dedicated body controller when body work begins. Each independent
  RS-485 bus needs a UART, a compatible transceiver, and transmit-direction
  control. Multiple motors share each bus using unique IDs; one UART per
  motor is unnecessary.
- Two buses (arms and legs) are a possible initial arrangement. Four buses
  (one per limb) would permit independent scheduling and better separation
  of bus faults. Final count depends on feedback rate, wiring and controller.
- A 3.3 V [MAX3485](https://www.analog.com/en/products/max3485.html)
  half-duplex RS-485 transceiver is a candidate for a microcontroller bus.
  Qualify the actual module, direction-control timing, and wiring before use.
  The Maestro's Pololu #2595 logic-level shifter does not implement RS-485.
- One [ROBOTIS U2D2](https://emanual.robotis.com/docs/en/parts/interface/u2d2/)
  is useful for initial computer-based motor identification. It requires an
  external motor supply. Its connector ports are not independent UART buses.
  Check the legacy RX-28 Molex connector against the U2D2's JST connector;
  obtain a compatible conversion harness rather than assuming physical fit.
- Plan power distribution separately from the data chain. Branch protection,
  cable ratings and power injection must follow measured current; do not
  assume the first motor's cable can carry every limb's current.

## Follow-up: face-only battery scope

The operator has approximately ten cells labelled 3.7 V / 1800 mAh and
explicitly limits their intended use to face/audio. Arms and legs can come
later. Manufacturer, chemistry, discharge rating, condition and protection
are still unknown. The operator's subsequent preference is two cells in
series (2S), stepped down to 6 V; this is the current candidate topology,
not a qualified assembly.

Each cell's nominal label energy is 6.66 Wh. Capacity in mAh does not establish
the peak current it can provide. Select cell count, protection, charging and
converters only after identifying the cells and measuring the face/audio load.
An individual 3.7 V cell cannot feed the previously proposed 5 V and 6 V
step-down regulators: a single-cell design needs step-up conversion for those
rails. A higher-voltage pack would need matched cells and its own suitable
protection/balancing and charger, with regulator dropout considered. The XIAO
battery charging connection is for a single cell, not a series pack.

For standard Li-ion/LiPo cells charging to 4.2 V each, the proposed 2S pair
would be 7.4 V nominal, 8.4 V fully charged, 1800 mAh and 13.32 Wh nominal.
Both cells carry the full pack current. Feed separate 6 V servo and 5 V
electronics regulators from the protected pack output. Use matched cells,
2S protection with balancing, an appropriate 8.4 V CC/CV charger, and a fuse
and switch sized to the qualified load. The protection board is not itself
a charger; do not use the XIAO's single-cell charging connection for 2S.

The [D42V55F6 documentation](https://www.pololu.com/product/5573)
requires input voltage above 6 V by its load-dependent dropout voltage.
Therefore a 2S buck design needs a load-qualified cutoff before servo
regulation is lost; pack sag and harness voltage drop also matter. No exact
cutoff is established from the nominal 7.4 V rating alone. A 6 V buck-boost
regulator is an alternative if maintaining 6 V at lower pack voltage is
required, still respecting each cell's discharge limit. Cell identity,
discharge capability, protection/charger parts and measured peak load remain
open before choosing components or assembling the pack.

## Cell label clarification: 2026-09-16

The operator reported `unr18659 1800mAh 6.66Wh`, then explicitly corrected the
marking to **IMR18650**. Preserve the correction; neither UNR18659 nor INR18650
is an established identity for these cells. The label energy/capacity imply
3.7 V nominal (6.66 Wh / 1.8 Ah). The operator subsequently confirmed the
brand as **Hongli**. Full suffix/lot code, condition, protection and the
applicable specification revision remain unverified.

A web-indexed manufacturer-authored Hongli specification matches the generic
designation **IMR18650-1800mAh**, 3.7 V / 6.66 Wh. It is titled
`SPECIFICATIONS FOR IMR18650-1800mAh`, identifies Xinxiang Hongli Supply Source
Technology Co., Ltd., and is dated 2024-05-10 (document A0 / 06210-012).
The indexed table lists 4.2 V charging, 360 mA recommended charging current,
900 mA maximum charging current, and 900 mA maximum discharge current.
Source: [Hongli specification](https://static.dianchi.cn/uploads/2024/08/13/d3ekjxlte973bebdx8.pdf).

The operator's confirmation of Hongli matches the manufacturer and model
designation in this specification; the exact lot/revision has not been
physically inspected.
The source figures were available in the web index; direct PDF retrieval
failed, so no full local datasheet inspection is claimed.

For cells covered by this Hongli specification, a fixed 1 A charger exceeds the
published 900 mA maximum. The DD23CRTA 0.5 A option is below that maximum but
above the 360 mA recommended rate; actual cell condition remains unverified.
At 1.8 Ah, 0.5 A is about 0.28C and 1 A is about 0.56C, and
each cell carries the full current in a 2S series pack. The possible 900 mA
discharge limit also means face/audio suitability cannot be inferred from the
IMR marking or capacity; compare verified cell limits against measured pack
current before accepting battery operation. No cell was charged or tested.

The operator asked whether two series cells halve the current per cell.
They do not: a 1 A pack charging current flows through each cell at 1 A.
Two matched cells in series give 7.4 V nominal / 8.4 V fully charged,
1800 mAh, with no increase in allowable current. Using the cited 900 mA
discharge limit gives 6.66 W at nominal pack voltage before converter losses;
available power falls with voltage. For a fixed load power, higher voltage
can reduce pack current through a converter, but series wiring does not
split that current. Face/audio peak demand still needs measurement.
