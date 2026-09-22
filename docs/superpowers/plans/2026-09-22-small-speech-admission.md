# Dedicated Small Speech-Admission Model Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Use subagent-driven-development when delegation is authorized in the next session. Steps use checkbox syntax. This plan does not switch the live backend by itself.

**Goal:** Replace routine Qwen speak/wait decisions with a measured small decision service, preferring local Kev and comparing original TypeSafe Jev when access is available.

**Architecture:** Deterministic audio/wake gates run before a compact decision context. A typed classifier answers a few independent questions; code combines their calibrated thresholds into SPEAK/WAIT. Qwen remains the reply writer and temporary comparison baseline, without automatic per-turn fallback calls.

**Tech Stack:** Existing Python/httpx decision adapter and isolated Kev Python 3.12 environment, pinned upstream Kev checkpoints, TypeSafe's documented System One API for Jev, synthetic JSONL evaluation fixtures, pytest and bounded subprocess resource measurements.

**Spec:** [ADR 0020](../../architecture/0020-conversation-expression-and-small-decisions.md), [ADR 0018](../../architecture/0018-audio-evidence-and-kev-admission.md), [handoff](../../checkpoints/2026-09-22-conversation-face-handoff.md).

## Global constraints

- User preference: a dedicated decision model for speaking; Qwen for substantive answers. The eight-second Qwen admission cap is temporary resilience, not the latency goal.
- Preserve wake requirement and 30-second follow-up window. Outside that window without a wake candidate, make no decision/reply request. Do not grant a window from VAD or a model error.
- Preserve local >=200 ms overlap, <120 ms speech and analysis-failure rejection. Unknown ASR/language confidence stays null; no invented confidence from VAD, language tags or another model.
- No unattended always-generating model loop. Classify a bounded finalized utterance; later partial-utterance experiments require their own evaluation.
- Same frozen cases, history, evidence and metrics for all candidates. Split by scenario and phrase family before tuning; no tuning on the locked confirmation set.
- Keep weights/environments and human data out of Git. Begin with original synthetic fixtures. Hosted Jev access must be configured intentionally; never send private room history to a new provider merely because a key exists.
- Run one challenger at a time. Do not repeat the previous 4B fp32 CPU memory-limit failures while local voices are loaded. Target local decision RSS <=2 GiB and MemAvailable >=6 GiB under the actual voice workload; larger candidates require a measured alternate host/profile.
- Candidate promotion targets, not existing achievements: warm p95 <=500 ms, p99 <=1 s, hard decision deadline <=1.5 s; false-SPEAK <=2%, valid-call recall >=95%, separately reported English/Cantonese recall >=90%. Freeze these before the confirmation run. Report uncertainty/sample counts, not “proved safe.”

## Sources and distinctions

[Kev](https://github.com/jaredpalmer/kev) is a locally runnable decision model family;
upstream serves typed questions in one forward pass. Reinspect/pin its current
revision instead of assuming a model alias still names the weights previously
tested. The tested 4B in this repo was a Qwen3-base CPU dynamic-int8 adaptation;
it is not a measurement of every current Kev release or deployment mode.

[Jev](https://docs.typesafe.ai/introduction) is TypeSafe's original System One
model. The [quickstart](https://docs.typesafe.ai/introduction/quickstart) documents
`POST https://api.typesafe.ai/v1/systemone` with an API key and `jev-latest`.
Its returned revision must be recorded. No public self-hosted weight route or
parameter count was established in this session. A Kev server accepting the
`jev-latest` alias is still Kev. [Confidence](https://docs.typesafe.ai/confidence)
summarizes the returned distribution and is not an independently verified accuracy.
Sources inspected 2026-09-22; recheck API/model revisions at execution.

## Task 1: Freeze context and evaluation cases

**Files:** Create `src/alice/conversation/decision_context.py`,
`tests/conversation/test_decision_context.py`,
`config/experiments/speech-admission-v2.json`,
`tests/fixtures/conversation/admission-development-v2.jsonl`,
`tests/fixtures/conversation/admission-confirmation-v2.jsonl`,
`scripts/evaluate_speech_admission.py` and its focused test. Read prior admission
and Diart reports before defining speaker fields.

**Interface:** `build_decision_context(text: str, history: list[tuple[str,str]], *, addressed: bool, active_conversation: bool, evidence: dict[str, object], heard_turns: list[dict[str, object]]) -> dict[str, object]` returns only bounded, whitelisted evidence. Classifier context is not a transcript log.

- [ ] Write failure cases for unknown confidence, NaN/infinite values, hostile control strings, excessive history, stale diarization and simultaneous speakers. Include this independent literal expectation:

```python
def test_missing_asr_confidence_stays_unknown():
    result = build_decision_context(
        "Alice, hello", [], addressed=True, active_conversation=False,
        evidence={"speech_probability_mean": 0.99}, heard_turns=[],
    )
    assert result["asr_confidence"] is None
    assert result["language_confidence"] is None
```

- [ ] Implement compact context: transcript <=240 characters, last two accepted exchanges <=120 characters per side, last four heard turns <=120 characters each, fixed English/Cantonese/unknown language label, three-decimal bounded audio statistics. Retain raw durations for deterministic overlap checks before rounding. Omit model SHA strings from the classifier prompt while preserving them in provenance.
- [ ] Speaker context fields are `speaker_label`, `speaker_status` (known/unknown/stale/mixed), `age_ms` and `addressee_relation` (active-speaker/other/unknown). Do not invent them when Diart is absent. Labels are session-local categories, not person identity. Unknown alone must not suppress a clear wake from a sole voice.
- [ ] Author 120 development cases and 120 disjoint confirmation cases, balanced between valid calls and negatives and between English and Cantonese. Cover exact wake, greeting, complete question, follow-up, unfinished sentence, background/media, peer-directed question/answer, language switch, ASR garbage, overlap and expired conversation. Keep paired counterfactuals within a split and record why each expected result follows from observable context.
- [ ] Add a scorer with explicit TP/FP/TN/FN denominators, language/scenario breakdown, bootstrap or Wilson intervals, p50/p95/p99 latency and timeout count. Ensure an all-WAIT classifier visibly fails recall and an all-SPEAK classifier visibly fails false-activation criteria.
- [ ] Run `uv run pytest tests/conversation/test_decision_context.py tests/conversation/test_admission_evaluation.py -q`; commit the frozen fixtures, config and scorer before candidate tuning. Save a SHA256 split manifest.

## Task 2: Audit Kev inference and compare atomic questions

**Files:** Modify `scripts/serve-kev-int8.py` only for demonstrated loader defects;
extend `scripts/evaluate_speech_admission.py`, `tests/conversation/test_admission.py`
and `test_models.py`; create `docs/experiments/2026-09-22-small-admission-v2.md`.
Keep the main application's dependency environment unchanged.

**Interface:** Preserve `Decision(advice, speak_probability, coherence_probability, decision_confidence, model)`. Candidate metadata additionally lives in the evaluation report: source/base/adapter/head revisions, dtype, device, token counts, truncation state, question template digest and warm/cold timing.

- [ ] Reproduce the previous pinned Kev result on the old fixtures once, keeping the old report intact. Inspect actual encoder token limits and truncation of the transcript/history/questions. Verify the intended base, adapter and pointer head are loaded together; do not accept a fallback smoke checkpoint.
- [ ] Compare a small pinned Kev candidate in its upstream supported precision against the CPU quantized loader on the same short synthetic inputs where memory permits. Record output divergence. For 4B, use a bounded alternate-host trial if necessary instead of triggering another local OOM. No changed precision is silently treated as equivalent.
- [ ] Evaluate three atomic typed questions together: “Is the current turn intelligible and complete?”, “Is this turn addressed to Alice?”, and “Does it invite a response now?” Use concise positive statements and explicit options. Apply audio/wake rules in code. Avoid one overloaded English instruction that asks a tiny model to reason about every policy and numeric threshold.
- [ ] Compare packed versus separate question inference and reverse Choice option ordering on development cases. Inspect paired disagreements, missing state and confident mistakes. Keep the initial `respond`/`coherent` formulation as a named baseline.
- [ ] Calibrate policy thresholds on development only. For the experiment, explicit code combines the three result scores:

```python
def combine_scores(scores, thresholds):
    required = ("coherent", "addressed", "response_due")
    if any(name not in scores for name in required):
        return "WAIT"
    return "SPEAK" if all(
        scores[name] >= thresholds[name] for name in required
    ) else "WAIT"
```

The production implementation also validates finite [0,1] values, model identity,
successful response framing and policy expiry. Write tests for each before coding;
missing/invalid output must never default to SPEAK. Scores remain model evidence.

- [ ] Run the scorer on development only; save every configuration and failure.
  Commit adapter/scorer improvements after tests and review. Pick at most one
  configuration per candidate for locked confirmation; do not repeatedly read the
  confirmation set while adjusting prompts.

## Task 3: Add original Jev comparison when access exists

**Files:** Create `src/alice/conversation/jev.py`, `tests/conversation/test_jev.py`;
extend the evaluation CLI, leaving the live default unchanged.

**Interface:** `JevDecisionClient` accepts an injected httpx client, explicit
endpoint/model and a secret provider; it returns the same `Decision` boundary.
Use current official response/schema documentation, not the Kev response validator
unchanged. Credentials stay in the process environment or secret store, never in
CLI arguments, reports, printed errors or model request fixtures.

- [ ] Read current primary API docs and record their date. If no API access is
  available, mark the Jev row unmeasured and proceed with Kev work; do not invent a
  local Jev model or block the entire plan waiting for an account.
- [ ] Write HTTP transport fixtures for valid typed results, provider revision,
  missing questions, invalid probabilities, authentication failure, timeout and
  cancellation. Assert no credential appears in emitted error/status events.
- [ ] Implement the minimal adapter. Run only synthetic development cases with
  the same compact context/questions/scorer and a recorded request/cost bound.
  A no-confidence Noul response remains a probability, not an absent score filled
  from another field.
- [ ] Freeze the chosen configuration and compare against Kev and temporary Qwen
  on the same locked confirmation set. Record endpoint end-to-end latency and
  network failures, not just provider-reported compute time.
- [ ] Run `uv run pytest tests/conversation/test_jev.py tests/conversation/test_models.py -q`
  and commit the adapter/report. Promotion is Task 4, not part of this comparison.

## Task 4: Shadow, promote or preserve a documented no-go

**Files:** Modify `conversation/models.py`, `runtime.py`, `cli.py` and dashboard
only after a candidate qualifies; extend `test_runtime.py`/`test_admission.py`.
Update ADR 0018/0020, runbook and the next-session checkpoint.

**Interface:** Existing selectable backend configuration and Decision contract;
new profile pins the chosen template/model/thresholds/deadline. Shadow results
cannot open a wake window, call the reply model or start TTS.

- [ ] Test shadow isolation, no request outside the wake gate, speaker evidence
  absent/stale, a turn expiring during evaluation and a late result after Stop.
  Keep default microphone draining through pending inference.
- [ ] If adding Diart context, first connect it in a bounded local shadow worker.
  Timestamp labels against the exact captured segment; preserve unknown/mixed
  intervals and first-speaker warmup. Compare text-only, oracle labels and actual
  Diart labels on separate rows; the oracle result is not end-to-end evidence.
- [ ] Run attended English/Cantonese and two-person tests, with explicit permission
  before retaining human recordings or uploading new-provider live context. Record
  derived decision/timing counts without transcripts by default.
- [ ] Promote only if frozen latency, resource, false-SPEAK and recall targets pass,
  including per-language results. On failure, publish a no-go and the next specific
  experiment (question formulation, speech-domain fine-tuning or alternate device),
  preserving the current explicit fallback. Do not lower thresholds just to make
  the visible demo answer more often.
- [ ] After promotion, keep Qwen as response writer and manual comparison backend.
  Candidate error/uncertainty means WAIT, not an automatic extra network model.
  Verify both accepted voices still work under load; document the rollback flag
  and a current service command with the chosen pin.

## Completion evidence

A dedicated admission service has one recorded model/template/threshold profile,
one locked evaluation report, resource/latency measurements under the real speech
workload, and attended false-activation/valid-wake feedback. No candidate is currently
certified by this plan. Keep the physical speech/face integration independently
testable so improving admission does not mask a playback or motion regression.
