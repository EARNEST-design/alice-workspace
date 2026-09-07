# Streaming Affect Motion v1

## Status

Research architecture only; not trained, evaluated, or approved for hardware
operation. The checked-in residual, face-event, head-gesture, and controller
response configurations contain conservative hand-authored priors. They are not
empirical performance or safety evidence.

## Intended use

This package format assembles the Alice face/head streaming motion components
for deterministic offline simulation, replay, and research evaluation. It
conditions bounded residual motion, explicit facial events, and semantic head
gestures on the versioned `affect-vector/v1` contract.

The loaded output remains a motion proposal. An independent trajectory
validator, safety supervisor, reviewed calibration, and deliberately enabled
adapter retain authority over any later actuation.

## Packaged components

- Safetensors residual-model weights.
- Fully resolved residual, face-event, head-gesture, and controller-response
  configurations.
- Exact affect schema, motion model, calibration, controller-response model,
  and controller-settings identities.
- Compact training record, external metrics reference, runtime/training seed
  policy, and SHA-256 checksum for every packaged artifact.

Loading validates the complete artifact inventory, checksums, and caller-
expected identities before constructing any software model component. Every
declared artifact must be a regular file and every filesystem entry must be
declared. Hashing, parsing, and model construction use one private immutable
byte snapshot, so a path changed during loading cannot replace validated
content. Loading does not import an Alice hardware module, open serial, access
a camera, or connect to ROS.

## Data and privacy

The package contains no training examples, raw participant media, biometric
observations, credentials, camera code, ROS code, or actuator adapter. Its
training record may identify an external dataset and permitted use so that a
retained run stays auditable; access and consent remain governed at that data
source. The training-record schema permits only bounded scalar settings,
bounded text references, exact identities, and finite non-negative loss values;
it has no field capable of carrying examples, media, or biometric arrays.

## Evaluation

No decision-grade metrics are currently reported. A retained candidate must
link immutable offline metrics and pass held-out replay checks for identity,
continuity, deterministic replay, controller-response error, motion
derivatives, latency, event frequency, refractory behavior, and conflicts.
Face and head results must be reported separately. Promotion additionally
requires consented, blinded human ratings that improve over the selected
baseline without worsening continuity or controller constraints.

## Limitations and exclusions

- This version covers only evidenced Alice face and head/neck channels, not
  full-body motion.
- Affect coordinates are conditioning requests, not claims about a person's
  internal emotional state.
- Seeded reproducibility depends on preserving generator state, the full
  identity set, exact weights, and resolved configurations.
- SHA-256 checksums detect corruption and substitution relative to the
  manifest; the manifest is not a cryptographic signature or trust root.
- No online learning or direct model authority over actuators is supported.

## Hardware boundary

Hardware use is out of scope for package loading and remains disabled by
default. Any future model trial requires the repository's explicit reviewed
bring-up procedure, exact calibration verification, operator approval, and the
master servo switch ready. This model card does not grant that approval.
