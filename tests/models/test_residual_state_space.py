from __future__ import annotations

from pathlib import Path

import pytest
import torch

from alice.models.residual_state_space import (
    ResidualStateSpace,
    ResidualStateSpaceConfig,
    load_residual_state_space_config,
)


def _config() -> ResidualStateSpaceConfig:
    return ResidualStateSpaceConfig(
        schema_version="residual-state-space-config/v1",
        model_id="residual-test-v1",
        affect_schema_id="affect-vector/v1",
        affect_dimensions=("valence", "arousal"),
        controller_response_model_id="controller-response-test-v1",
        calibration_sha256="a" * 64,
        controller_settings_sha256="b" * 64,
        actuator_names=("mouth_open", "neck_rotation"),
        hidden_size=5,
        residual_envelope=(0.05, 0.2),
        research_status="unfitted-research-prior",
        provenance="Hand-authored unit-test priors; not empirical evidence.",
    )


def _features(model: ResidualStateSpace, *, batch: int = 2) -> torch.Tensor:
    steps = 4
    return model.compose_features(
        affect=torch.full((batch, steps, 2), 0.25),
        intensity=torch.full((batch, steps, 1), 0.6),
        anchor_pose=torch.full((batch, steps, 2), 0.1),
        response_position=torch.full((batch, steps, 2), 0.05),
        response_velocity=torch.full((batch, steps, 2), -0.02),
        elapsed_s=torch.full((batch, steps, 1), 0.1),
    )


def test_residual_is_bounded_per_actuator() -> None:
    """Removing the envelope projection would permit unsafe residual magnitudes."""

    model = ResidualStateSpace(_config())
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.fill_(4.0)

    residual, hidden = model(_features(model), hidden=None)

    envelope = torch.tensor((0.05, 0.2)).view(1, 1, 2)
    assert residual.shape == (2, 4, 2)
    assert hidden.shape == (1, 2, 5)
    assert torch.all(residual.abs() <= envelope + 1e-7)
    assert torch.all(residual[:, -1].abs() > 0.99 * envelope.expand(2, 1, 2)[:, 0])


def test_loading_weights_cannot_replace_configured_envelope() -> None:
    """A same-shape checkpoint from another config must not expand output bounds."""

    source = ResidualStateSpace(
        _config().model_copy(update={"residual_envelope": (0.9, 0.9)})
    )
    target = ResidualStateSpace(_config())

    target.load_state_dict(source.state_dict())

    assert torch.equal(target.residual_envelope, torch.tensor((0.05, 0.2)))


def test_state_carry_matches_one_contiguous_rollout() -> None:
    """Resetting the GRU between accepted prefixes would break replay continuity."""

    torch.manual_seed(7)
    model = ResidualStateSpace(_config())
    features = _features(model, batch=1)

    contiguous, contiguous_hidden = model(features, hidden=None)
    first, boundary_hidden = model(features[:, :2], hidden=None)
    second, resumed_hidden = model(features[:, 2:], hidden=boundary_hidden)

    assert torch.allclose(torch.cat((first, second), dim=1), contiguous)
    assert torch.allclose(resumed_hidden, contiguous_hidden)


def test_compose_features_keeps_every_semantic_condition_at_each_step() -> None:
    """Dropping any required condition would change the hand-derived feature row."""

    model = ResidualStateSpace(_config())
    features = model.compose_features(
        affect=torch.tensor([[[0.1, -0.2]]]),
        intensity=torch.tensor([[[0.7]]]),
        anchor_pose=torch.tensor([[[0.3, 0.4]]]),
        response_position=torch.tensor([[[0.5, 0.6]]]),
        response_velocity=torch.tensor([[[0.7, 0.8]]]),
        elapsed_s=torch.tensor([[[0.02]]]),
    )

    assert features.shape == (1, 1, 10)
    assert features[0, 0].tolist() == pytest.approx(
        [0.1, -0.2, 0.7, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.02]
    )


def test_compose_features_rejects_non_finite_elapsed_time() -> None:
    """A non-finite time condition must not contaminate recurrent state."""

    model = ResidualStateSpace(_config())
    with pytest.raises(ValueError, match="finite"):
        model.compose_features(
            affect=torch.zeros((1, 1, 2)),
            intensity=torch.zeros((1, 1, 1)),
            anchor_pose=torch.zeros((1, 1, 2)),
            response_position=torch.zeros((1, 1, 2)),
            response_velocity=torch.zeros((1, 1, 2)),
            elapsed_s=torch.tensor([[[float("nan")]]]),
        )


def test_config_binds_controller_response_feature_identity() -> None:
    """Controller state from a different calibration must not look compatible."""

    values = _config().model_dump()
    values.update(
        {
            "controller_response_model_id": "controller-response-v1",
            "calibration_sha256": "a" * 64,
            "controller_settings_sha256": "b" * 64,
        }
    )

    config = ResidualStateSpaceConfig.model_validate(values)

    assert config.controller_response_model_id == "controller-response-v1"
    assert config.calibration_sha256 == "a" * 64
    assert config.controller_settings_sha256 == "b" * 64


def test_config_records_durable_research_provenance() -> None:
    """An unfitted architecture must not serialize as promotion evidence."""

    values = _config().model_dump()
    values.update(
        {
            "research_status": "unfitted-research-prior",
            "provenance": (
                "Hidden size and envelopes are hand-authored priors; "
                "not empirical or promotion evidence."
            ),
        }
    )

    config = ResidualStateSpaceConfig.model_validate(values)

    assert config.research_status == "unfitted-research-prior"
    assert "not empirical or promotion evidence" in config.provenance


def test_versioned_config_loads_without_hardware_access() -> None:
    """The checked-in model contract must be parseable as an offline artifact."""

    path = Path("config/models/residual-state-space-v1.yaml")
    config = load_residual_state_space_config(path)

    assert config.model_id == "residual-state-space-v1"
    assert config.research_status == "unfitted-research-prior"
    assert "not empirical or promotion evidence" in config.provenance
    assert len(config.actuator_names) == len(config.residual_envelope)
    assert config.feature_size == (
        len(config.affect_dimensions) + 3 * len(config.actuator_names) + 2
    )


def test_forward_rejects_non_finite_parameters() -> None:
    model = ResidualStateSpace(_config())
    with torch.no_grad():
        next(model.parameters()).view(-1)[0] = float("inf")
    features = torch.zeros((1, 1, model.config.feature_size))

    with pytest.raises(ValueError, match="finite"):
        model(features, None)
