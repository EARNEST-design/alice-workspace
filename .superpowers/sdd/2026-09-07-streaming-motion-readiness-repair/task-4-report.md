# Task 4 report — read-only connected-device and replay readiness gate

## Outcome

Implemented and committed as `63f10f4` (`feat: add streaming motion readiness
gate`). The new `alice-motion-readiness` command validates the immutable
hardware manifest and model package, resolves the Pololu command interface and
C525 camera through stable by-id links plus sysfs metadata, binds exact model,
calibration, firmware-setting, and controller-response identities, and performs
deterministic production/controller-response replay.

The command has no hardware-adapter construction, serial open/write, Set Target,
camera open, or frame-access path. Its JSON artifact contains only check results,
non-secret identities, hashes, virtual replay dimensions, and a replay digest.
Hardware execution remains a separately reviewed command boundary documented in
`hardware/bringup/streaming-affect-motion-v1.md`.

## RED / GREEN

RED after adding the readiness and installed-wheel behavior tests, before the
entry point existed:

```text
uv run pytest tests/experiments/test_motion_readiness.py tests/packaging/test_installed_wheel.py -v
ERROR tests/experiments/test_motion_readiness.py
ModuleNotFoundError: No module named 'alice.experiments.motion_readiness'
```

During GREEN, the first collected run exposed two test-harness import/name
errors, which were corrected without weakening expectations. The next behavior
run exposed an invalid attempt to feed already realized production outputs back
as fresh controller commands. The replay was corrected to use the production
composer's controller-response state directly; this is the actual runtime
boundary required by the design.

Targeted GREEN:

```text
uv run pytest tests/experiments/test_motion_readiness.py tests/packaging/test_installed_wheel.py -v
9 passed in 8.61s
```

The tests use synthetic by-id/sysfs trees and an integrity-checked synthetic
model package. They patch serial construction/write, Maestro adapter
construction, Set Target encoding, OpenCV capture construction, camera open,
and frame read to raise immediately if any forbidden path is reached. Missing
or wrong device identities, exact package/calibration drift, and replay faults
all produce failed structured checks; the CLI returns exit `2` for a failed
report.

## Verification

Final required verification immediately before commit:

```text
uv run pytest -q && uv run ruff check src tests && uv run mypy src && git diff --check
594 passed, 2 warnings in 15.44s
All checks passed!
Success: no issues found in 59 source files
```

The two warnings are the existing Python 3.13 `os.fork()` deprecation warnings
in hardware-identification tests; there were no failures.

The explicit CLI was also run against the currently connected stable Pololu
interface-00 and Alice-facing C525 video-index0 paths, using a synthetic valid
`motion-model-package/v2`. It inspected only filesystem/sysfs metadata and ran
software replay:

```text
status=pass
checks=hardware-manifest:pass,controller-identity:pass,camera-identity:pass,package-identities:pass,motion-replay:pass
replay_digest_sha256=075212999de9db96ec8b4317b74fd7a71fbd5f39a3a78dd90488d5fc92c080bf
prefix_count=300
```

The physical servo master-switch position is not software-observable. The
procedure requires it to remain OFF; this command was safe independently of
that assumption because no serial/camera node was opened and no command was
encoded or issued.

## Fix round 1 — reviewer remediation and handoff audit

The first implementer’s uncommitted remediation added an explicit reviewed
device-identity configuration, canonical controller/camera path checks,
kernel-device and USB identity checks, descriptor-anchored package byte
snapshots, exclusive no-replace report publication, input/output inode alias
protection, and structured failure codes. The inherited focused check reported
`61 passed` before this handoff.

The handoff audit found one remaining boundary violation: readiness and package
readers acquired `O_RDONLY` descriptors before confirming that supplied paths
were regular files. A supplied `/dev/*` path or FIFO could therefore be opened
read-capably before fail-closed validation. Two test-first regressions were
added and observed RED against that inherited code:

```text
uv run pytest tests/experiments/test_motion_readiness.py::test_special_input_is_inspected_without_a_read_capable_open tests/models/test_package.py::test_snapshot_inspects_special_artifacts_without_a_read_capable_open -v
2 failed
```

The readers now obtain Linux `O_PATH | O_NOFOLLOW` descriptors first, reject
every non-regular inode without a read-capable open, and only then reopen the
same verified regular inode through its descriptor. This preserves inode
identity checks and the byte snapshot, so a post-snapshot pathname substitution
cannot become an ABA load. The GREEN rerun passed:

```text
2 passed
```

The complete focused remediation scope then passed:

```text
uv run pytest tests/experiments/test_motion_readiness.py tests/models/test_package.py tests/packaging/test_installed_wheel.py -v
63 passed in 12.32s
```

The previously untracked
`config/experiments/streaming-motion-readiness-v1.yaml` is intentional. It is
the versioned reviewed identity authority for the exact Pololu command-port and
C525 capture node (by-id path, USB vendor/product/serial/interface, and camera
index). The CLI requires it, and the bring-up procedure now declares it and
passes it explicitly; a CLI camera argument cannot override it.

Ruling: inspect every arbitrary input with `O_PATH` before requesting read
access — prevents a readiness check from triggering device/FIFO I/O before its
file-type gate — cost if wrong: the gate is intentionally Linux-specific and
fails closed where `O_PATH` and descriptor reopening are unavailable.

Ruling: retain exclusive `link`-based publication and the captured
file/directory inode sets — a report can neither replace an existing output nor
be added through an input-package directory or hard-link alias — cost if wrong:
operators must select a new, real output directory rather than reuse a prior
artifact name or symlinked convenience path.

Ruling: bind device evidence to the checked-in config, canonical by-id spelling,
resolved character-device major/minor, and matching sysfs USB ancestry — rejects
filename mimicry and controller/camera substitution without opening either node
— cost if wrong: a reviewed hardware replacement requires a config update and a
new readiness artifact.

Ruling: collapse all readiness check exceptions into fixed check IDs, fixed
error codes, and fixed reason strings; publication failures emit only their
fixed error code — prevents paths, exception values, and package/device details
from entering failed reports or CLI output — cost if wrong: diagnosis requires
local operator logs and a controlled inspection rather than relying on the
artifact alone.

## Final remediation verification

Immediately before the remediation commit:

```text
uv run pytest -q && uv run ruff check src tests && uv run mypy src && git diff --check
618 passed, 2 warnings
All checks passed!
Success: no issues found in 59 source files
```

The warnings are the existing Python 3.13 multi-threaded `os.fork()`
deprecations in hardware-identification tests; there were no test failures.

With both reviewed stable selectors present, a metadata-only connected
invocation was run using a deliberately nonexistent package and a new temporary
report path. It returned exit `2` as expected, passed hardware-manifest,
device-config, controller-identity, and camera-identity, and produced only the
fixed `package-identities-invalid` and `replay-prerequisite-failed` failure
codes. No package was loaded, no replay ran, and the implementation was audited
to avoid any serial or V4L2 device-node open on this path.

## Independent repair-range review

Reviewed the repair plan, design spec, commits `2879162..63f10f4`, and the Task
1–3 reports. The range preserves complete replay state, performs atomic
controller-feasible fallback, resumes ordinary head gestures from actual poses,
binds training and packages to the full controller-response digest, and keeps
the readiness boundary read-only. The full suite and connected metadata/replay
gate provide evidence for the stated hardware-readiness objective. No blocking
requirement gap was found. This is readiness evidence, not a model-promotion
claim or actuation permit.

## Assumptions

- The reviewed controller command interface remains Pololu serial `00037376`,
  USB interface `00`; any serial/interface drift fails closed.
- The operator selects the Alice-facing camera by its complete V4L2 by-id name;
  video-index0 is the capture node and a direct `/dev/videoN` selector is not
  accepted.
- Each listed seed receives a 60-second virtual replay across all three affect
  vectors; `300` prefixes therefore represent two independently replayed seed
  trajectories at a 0.4-second prefix.
- Package manifest/config checksums and validated snapshots are the trust
  boundary. Package signing is outside this scoped readiness repair.
- The connected verification used a deterministic synthetic research package,
  so it proves the gate and current identities work; it does not promote that
  package for a hardware trial.

Ruling: require both stable by-id syntax and matching sysfs ancestry for the
controller and camera — rejects stale links, wrong Pololu serial/interface, and
unstable direct device names without opening a node — cost if wrong: non-Linux
or non-udev environments need a separate reviewed discovery implementation.

Ruling: compare the hardware calibration, actuator order, firmware settings,
controller-response model, complete response digest, and package identities
before replay — makes a passing report specific to one exact configuration —
cost if wrong: any identity-bearing metadata/config revision requires a new
package and readiness artifact even when behavior is otherwise equivalent.

Ruling: replay the production composer twice for 60 virtual seconds per seed and
compare canonical digests — tests indefinite stateful planning, exact
controller-response integration, and determinism without wall-clock delay —
cost if wrong: two checked-in seeds and three vectors do not cover every valid
affect trajectory, so newly discovered edge cases require new representative
fixtures.

Ruling: keep the console wrapper free of ML imports until after argument parsing
— preserves base-wheel `--help` and ordinary import behavior while requiring the
`ml` extra only for an actual replay — cost if wrong: invoking readiness without
the optional ML dependencies fails at execution rather than package install.

Ruling: emit compact derived JSON and keep hardware execution in the existing
separate guarded CLI — provides auditable automation evidence without frames,
raw device traffic, or actuator authority — cost if wrong: operators must carry
the readiness artifact into the later approval workflow manually.
