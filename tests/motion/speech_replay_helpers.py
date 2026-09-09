"""Repository qualification fixture, never a hardware or trained-model runner.

Reuses the exact zero-residual baseline qualified by test_composed_streaming.
The optional neutral-priors mode deliberately does not condition on speech affect.
"""

import hashlib
from collections import Counter
from datetime import datetime, timezone
from typing import Literal

import yaml
from test_composed_streaming import CONFIG, _baseline, _intent

from alice.contracts.affect import AffectVectorSchema
from alice.contracts.motion import TargetUpdateHorizon
from alice.motion.intent_filter import IntentFilter, IntentFilterConfig
from alice.motion.state import dump_state, load_state
from alice.speech.timeline import PreparedSpeech


def replay_speech(
    speech: PreparedSpeech,
    *,
    mode: Literal["speech-filtered", "neutral-priors-only"],
):
    if mode not in {"speech-filtered", "neutral-priors-only"}:
        raise ValueError("unknown speech replay mode")
    baseline = _baseline(speech.plan.seed)
    restarted = _baseline(speech.plan.seed)
    state = baseline.initial
    affect = CONFIG.parent / "affect"
    config = IntentFilterConfig.model_validate(
        yaml.safe_load((affect / "intent-filter-v1.yaml").read_text())
    )
    if config.retained_training_coordinates:
        raise ValueError("this baseline replay requires an empty affect support set")
    intent_filter = IntentFilter(
        schema=AffectVectorSchema.model_validate(
            yaml.safe_load((affect / "affect-vector-v1.yaml").read_text())
        ),
        config=config,
    )
    updates = []
    requested = set()
    accepted = set()
    intents = Counter()
    proposals = Counter()
    events = set()
    duration_ns = round(len(speech.audio.pcm) / speech.audio.sample_rate * 1e9)
    while state.monotonic_ns < duration_ns:
        sample = state.monotonic_ns * speech.audio.sample_rate // 1_000_000_000
        request = speech.affect_intent(
            sample,
            now_ns=state.monotonic_ns,
            captured_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
        )
        assert request is not None
        requested.add((request.vector, request.intensity))
        intent = (
            intent_filter.update(request, state.filtered_intent, state.monotonic_ns)
            if mode == "speech-filtered"
            else _intent(state.monotonic_ns)
        )
        accepted.add((intent.vector, intent.intensity))
        intents[intent.support_status.value] += 1
        prefix, boundary = baseline.runtime.replan(intent, state, state.monotonic_ns)
        restored = load_state(dump_state(state))
        assert restarted.runtime.replan(intent, restored, restored.monotonic_ns) == (
            prefix,
            boundary,
        )
        proposals[baseline.composer.last_status] += 1
        for update in prefix.updates:
            # Integer nanoseconds avoid duplicate float boundary samples.
            absolute_ns = prefix.starts_at_ns + round(update.offset_s * 1e9)
            absolute = update.model_copy(update={"offset_s": absolute_ns / 1e9})
            if updates and absolute.offset_s == updates[-1].offset_s:
                assert absolute.targets == updates[-1].targets
            else:
                updates.append(absolute)
        events.update(
            record.model_dump_json()
            for record in boundary.event_history
            if record.event_type in {"face-event/v1", "head-gesture/v1"}
            and record.started_monotonic_ns < boundary.monotonic_ns
        )
        state = boundary
    return TargetUpdateHorizon(
        schema_version="target-update-horizon/v1", updates=tuple(updates)
    ), {
        "mode": mode,
        "model": state.model_id,
        "config_hashes": baseline.config_hashes,
        "affect_input_hashes": {
            name: hashlib.sha256((affect / name).read_bytes()).hexdigest()
            for name in ("affect-vector-v1.yaml", "intent-filter-v1.yaml")
        },
        "seed": speech.plan.seed,
        "trained_expression_model": False,
        "requested_affect_drives_motion": False,
        "intent_status_counts": dict(intents),
        "proposal_status_counts": dict(proposals),
        "requested_affect_changed": len(requested) > 1,
        "accepted_affect_changed": len(accepted) > 1,
        "motion_event_count": len(events),
        "exact_restart_equal": True,
        "actuation_mode": "none",
    }
