# Selected-face speech checkpoint

Saved 2026-09-11 in the preserved `feature/streaming-affect-motion` worktree.
Task 6's implementation is committed at `7fe8264`; subsequent tuning is recorded
with this checkpoint. Locate the latest checkpoint commit with
`git log -1 --format='%h %s' -- SESSION_CHECKPOINT.md`. Do not reset the worktree.

**Software qualified; latest physical tuning awaiting visible-motion validation.**
879 tests passed, two existing fork warnings; Ruff and mypy (85 files) clean.
Independent reviewer found no remaining blockers. See the
[experiment report](../experiments/2026-09-11-selected-face-speech.md),
[ADR0011](../architecture/0011-selected-face-speech-execution.md) and
[procedure](../../hardware/bringup/face-speech-trial-v1.md).

## Operator feedback and current state

The user explicitly requested Task 6 with servos on. Initial combined trials
completed at Home. After a repeat with audible headphones, the user said timing
looked OK but wanted a quicker mouth and more visible expression. A stronger
trial then prompted: “blinking doesn't happen fully and the frown needs mouth
closed to show sadness”.

The latest candidate has full calibrated blinking and a closed-mouth frown
after the final sad line. The controller run completed and returned to Home,
but camera evidence showed almost no face motion despite changing PWM. **Do
not call this physical acceptance.** The current question asks whether servo
power is still on. Await the operator's answer before another run. No controller
or camera process remains open. Preserve all artifacts and failed simulations.

## Current candidate

Scope: mouth 6, lids 3/4, forehead 5, corners 9/11; no gaze 8/10 or head 0/1/2.
Azelma, full calibrated jaw range, runtime jaw 0/0, accepted 100 ms lead remain.
The candidate envelope is 10/30 ms attack/release, expression jaw bias 0; corner
range ±.8 with prior .8/s and 4/s² caps; authored scale/intensity 1, transition .8 s.
Full lids use range ±1, step .1, rate 1.5/s, acceleration 8/s², response .1 s, retained
firmware 0/0; authored blink onset 1 s, hold .8 s, release 1 s. Forehead stays neutral.

`--sad-hold-s 1.2` adds a closed-jaw(-1), complementary-corner(-.8/+.8) pose after
successful final negative audio, clearing other selected channels. Dwell starts
at commanded rest with matching controller PWM, bounded to 6 s; then Home and
jaw 0/11 restoration. Fault/cancel sends no Home or restoration writes. PWM is
not measured mechanical arrival. The overall 25 s run limit remains active.

The original baseline configs remain unchanged. Candidate versioned files live
in `config/speech/`; the retained full config is
`artifacts/speech/face-integration-2026-09-11/full-blink-config/`.

## Next attended run

After the pending power/motion issue is resolved, use a **new** output directory:

```bash
HF_HUB_OFFLINE=1 uv run --extra speech python \
  artifacts/speech/face-integration-2026-09-11/observe_visible_trial.py \
  artifacts/speech/face-integration-2026-09-11/azelma-full-blink-hardware-camera-2 \
  config/speech/stream-visible-demo-v1.jsonl \
  --config-root artifacts/speech/face-integration-2026-09-11/full-blink-config \
  --sad-hold-s 1.2
```

This uses the validated C525 only, actual speaker and selected servos. Inspect
child manifest even if observer exits 0. Ask whether full closure and final
sadness are visible; blink is deliberately slow during this check. Preserve the
accepted mouth timing; tune natural blink timing only after closure is observed.
Standing attended-run authorization persists; do not repeat readiness checklists.

Learned emotion remains honest neutral fallback; no fitted package or live LLM
provider has been added. Tasks 1–5 remain qualified. Do not rerun completed ML
repairs, clear experiments, merge or discard this worktree.
