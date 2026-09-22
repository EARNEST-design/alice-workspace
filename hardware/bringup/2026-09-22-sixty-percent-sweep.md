# Attended 60% motor sweep — 2026-09-22

The operator requested each motor move 60% of its range in both directions.
Ran once on Mini Maestro 12 serial `00037376` through native USB. All 11
connected channels completed, in order: 0–6, 8–11. Channel 7 remained off.

Each motor followed Home → lower 60% → Home → upper 60% → Home, with each
percentage measured from Home toward the corresponding software limit in
`hardware/alice-face-v1.yaml`. In particular, channel 2 used the software upper
limit 8000 qus rather than the firmware upper limit 8832. Runtime speed was 5,
acceleration 2, hold 0.7 seconds per leg. The controller-output settling bound
was 12 qus (3 microseconds), five consecutive samples, 12-second leg timeout,
and 180-second total deadline. Native USB allows the saved UART configuration
to remain unchanged. Protocol reference: official [Pololu USB SDK](https://github.com/pololu/pololu-usb-sdk/blob/master/Maestro/Usc/Usc.cs).

Preflight found the expected device identity, no competing device owner, the
expected calibration, all connected outputs exactly Home, script stopped, and
zero errors. Six offline checks covered target calculations, rejected initial
states, full sequence/restoration, cancellation, controller faults, and ambiguous
USB failures before motion. Errors/timeouts/cancellation stop the sequence;
healthy USB permits disabling the active output. Ambiguous I/O errors prohibit
further writes and require the operator's power stop.

The sequence completed all 44 legs in 110.594 seconds. Forty-three recorded
outputs exactly matched their targets; channel 11 lower target 5282 reported
5292 qus, a 2.5-microsecond difference within the settling bound. Independent
readback after the runner closed confirms every connected output exactly Home,
all original runtime speed/acceleration restored, all persistent parameters
unchanged, channel 7 unchanged/off, script stopped, and errors zero. The saved
UART fixed-9600 configuration was retained. No firmware write occurred.

Controller readbacks describe generated pulse outputs, not physical joint
positions. No visual acceptance or mechanical-quality feedback was received
during the run.

Config, runnable script, six checks, initial/final snapshots, 44 leg records,
metrics, conclusion, and SHA-256 manifest are retained in the ignored directory
`artifacts/motion/2026-09-22-sixty-percent-01/`. This run did not alter the
speech baseline or existing experiment artifacts.
