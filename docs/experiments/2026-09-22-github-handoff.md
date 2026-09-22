# GitHub synchronization and next-session handoff, 2026-09-22

## Scope

The operator requested synchronization of all local work to the existing public
`EARNEST-design/alice-workspace` GitHub repository and a saved next-session plan.
Preserve the five branch boundaries and existing worktrees; no branch merge,
force-push, hardware motion or new model deployment is part of this task.

The speech branch contains the working bilingual wake-gated conversation bench,
Qwen ASR/admission/reply adapters, local voices, dashboard/ROS observer, overlap
checks, bounded capture/lifecycle fixes, isolated Kev/Diart runners, locked extra
dependencies and reviewed experiment/hardware notes. Main contains planning entry
points and historical architecture work. Clean passive-observation, actuator and
robot-description branch tips are retained as their own branches.

Before sync the published remote had only initial main and the actuator branch.
Local main was eight commits ahead, and the speech, passive and description tips
were unpublished. Publication uses normal fast-forward/new-branch pushes and
verifies all five remote tips against local commit IDs after pushing.

## Fresh validation

- Host Python 3.13 suite excluding ROS/URDF-only imports: **1,128 passed**, two
  existing fork-with-multiple-threads warnings, 70.33 s.
- ROS/URDF suite against read-only current sources in qualified test image
  `sha256:24a9c3325518bc673df6ca746fa7feddcd4760871e3f270f376779f55e2ec42a`:
  **199 passed**, two container-only skips, two packaging setup errors. The errors
  were an omitted offline build-cache mount, not test assertion failures.
- Restoring the documented cache mount reran the two failed wheel metadata tests:
  **2 passed**, 0.75 s.
- Host packaging suite: **4 passed**, 0.94 s, including its container-skipped Docker
  build-context test. The other container-skipped Compose fixture test is checked
  on the host with its installed image: **7 passed**, 1.05 s.
- Ruff on src/tests/scripts passes; all 29 conversation/script formatting checks
  pass; strict mypy passes all 101 core source files; uv lock check resolves the
  existing 80-package lock unchanged; git diff whitespace check passes.

A naive initial host-only full-suite command could not collect generated ROS
interfaces, ROS node modules and xacro. The supported split above supplies those
through the qualified container. Preserve the failed wrapper logs rather than
misreporting one universal command as passing on the host.

Validation logs and a SHA256 manifest remain in ignored
`artifacts/handoff/2026-09-22/`. No test accessed physical actuators or recorded
human input. The current conversation service was not restarted by this task.

## Published and local-only material

Publication review found no credential values, raw human recordings/transcripts,
biometric arrays, model binaries or vendored model sources among the changed files.
Only source, tests, dependency metadata, authored synthetic inputs, technical
reports and plans are selected for the index. Historical local commits are also
checked for high-confidence credential markers before publishing their branch tips.

The unanchored `checkpoints/` ignore pattern also ignores new Markdown handoffs.
The exact authored `docs/checkpoints/2026-09-22-conversation-face-handoff.md` is
therefore force-added individually; the rule is not broadly relaxed.

Ignored experiment artifacts, raw data, downloaded models, machine environments,
local calibration and credentials stay local. Their provenance paths/hashes and
conclusions are recorded in the committed reports. GitHub synchronization is not
an off-machine backup of these excluded assets.

## Next session

Read [the handoff](../checkpoints/2026-09-22-conversation-face-handoff.md), then
[ADR 0020](../architecture/0020-conversation-expression-and-small-decisions.md).
The two executable plans cover:

1. [Conversation to synchronized mouth/face](../superpowers/plans/2026-09-22-conversation-face-integration.md):
   verified DAC route, committed delivery clauses, bilingual ROS profiles, one
   RunSpeech authority, visual feedback, simulated then attended selected-face tests.
2. [Small dedicated admission](../superpowers/plans/2026-09-22-small-speech-admission.md):
   compact context, atomic questions, pinned Kev/precision audit, original hosted
   Jev comparison when access exists, held-out latency/resource/activation gates.

The preference for a smaller decision model is explicit. Qwen remains the temporary
live admission baseline until a replacement qualifies, and remains the response
writer afterward. No candidate or physical expression integration is declared
complete by publishing this plan.

## Independent review

Publication review found no important data/source/dependency blockers. The scoped
plan review identified three integration gaps and verified their corrections:
ROS output has its own route/gain boundary; Cantonese needs bounded PREPLAY before
actuator authority; physical ROS admission stays blocked until complete-host
ownership visibility qualifies. No remaining important findings in that scope.
