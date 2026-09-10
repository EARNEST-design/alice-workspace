"""Persistent production motion follows audible affect, never generated lookahead."""

from pathlib import Path

import pytest

from alice.speech.composer import compose_frame
from alice.speech.expression_bridge import ExpressionBridge
from alice.speech.timeline import SpeechFrame


def frame(sample, valence=0.7, aperture=0.4, weight=1):
    return SpeechFrame(
        sample_index=sample,
        mouth_aperture=aperture,
        speech_weight=weight,
        vector=(valence, 0.2, 0),
        intensity=0.7,
    )


def bridge(mode="authored"):
    return ExpressionBridge(
        config_root=Path("config"),
        seed=7,
        mode=mode,
        generation_id="test",
        origin_ns=1000000000,
    )


def positions(update):
    return {t.actuator_name: t.normalized_position for t in update.targets}


def test_clock_never_accepts_speculative_state_and_authored_cues_change_corners():
    live = bridge()
    positive, negative = [], []
    for sample in range(0, 24000 * 8, 480):
        f = frame(sample, 0.7 if sample < 96000 else -0.6)
        output = live.advance(f, sample, 24000)
        assert live.state.monotonic_ns <= 1000000000 + sample * 10**9 // 24000
        p = positions(output)
        assert len(p) == 11
        assert all(
            p[name] == 0 for name in ("neck_rotation", "head_tilt", "face_pitch")
        )
        (positive if sample < 96000 else negative).append(p["left_mouth_corner"])
    assert max(positive) > 0.1 and min(positive) >= 0
    assert min(negative) < -0.1
    assert live.identity["trained_expression_model"] is False
    assert live.identity["source"] == "authored-expression/v1"


def test_blink_phase_and_all_nonjaw_targets_survive_speech_composition():
    live = bridge()
    seen_blink = False
    for sample in range(0, 24000 * 12, 480):
        f = frame(sample, 0.7 if sample < 72000 else -0.6)
        expression = live.advance(f, sample, 24000)
        composed = compose_frame(expression, f, live.sync_config)
        before, after = positions(expression), positions(composed)
        assert all(before[n] == after[n] for n in before if n != "mouth_open")
        assert -1 <= after["mouth_open"] <= 1
        seen_blink |= before["upper_eyelids"] < -0.1
    assert seen_blink
    f = frame(24000 * 12, weight=0, aperture=0)
    expression = live.advance(f, f.sample_index, 24000)
    assert (
        positions(compose_frame(expression, f, live.sync_config))["mouth_open"]
        == positions(expression)["mouth_open"]
    )


def test_snapshot_restores_pending_prefix_rng_and_sparse_pose():
    live = bridge()
    for sample in range(0, 36001, 480):
        live.advance(frame(sample), sample, 24000)
    restored = bridge()
    restored.restore(live.snapshot())
    for sample in range(36480, 24000 * 7, 480):
        f = frame(sample, -0.6 if sample >= 48000 else 0.7)
        assert restored.advance(f, sample, 24000) == live.advance(f, sample, 24000)
    assert restored.snapshot() == live.snapshot()


def test_learned_mode_has_no_fabricated_support_and_cancel_rejects_late_work():
    live = bridge("learned-fallback")
    for sample in range(0, 24000 * 2, 480):
        assert set(positions(live.advance(frame(sample), sample, 24000)).values()) == {
            0
        }
    assert live.state.filtered_intent.support_status.value == "fallback"
    assert live.state.filtered_intent.support_distance is None
    with pytest.raises(ValueError):
        live.advance(frame(0), 48000, 24000)
    with pytest.raises(ValueError):
        live.advance(frame(0), 0, 24000)
    live.cancel()
    with pytest.raises(RuntimeError, match="cancelled"):
        live.advance(frame(48000), 48000, 24000)
    with pytest.raises(ValueError, match="identity"):
        bridge().restore(live.snapshot())


def test_replanning_does_not_accumulate_absolute_blink_or_gaze_offsets():
    live = bridge()
    eyelids, gaze = [], []
    for sample in range(0, 24000 * 12, 480):
        p = positions(live.advance(frame(sample), sample, 24000))
        eyelids.append(p["upper_eyelids"])
        gaze.append(p["right_eye_horizontal"])
    # Configured event amplitudes are <= .5 for blinks and <= .16 for gaze;
    # neutral eye anchors must not absorb those offsets on each 400 ms replan.
    assert min(eyelids) >= -0.5 - 1e-6
    assert max(abs(v) for v in gaze) <= 0.16 + 1e-6
    assert any(v < -0.1 for v in eyelids)
    assert any(abs(v) < 1e-4 for v in eyelids[100:])


def test_stale_source_completes_accepted_blink_without_new_events():
    live = bridge()
    eyelids = []
    for sample in range(0, 24000 * 6, 480):
        update = live.advance(frame(sample), sample, 24000, source_fresh=sample < 24000)
        eyelids.append(positions(update)["upper_eyelids"])
    assert min(eyelids[:80]) < -0.1
    assert all(abs(value) < 1e-4 for value in eyelids[150:])
    assert live.state.filtered_intent.support_status.value == "stale"
