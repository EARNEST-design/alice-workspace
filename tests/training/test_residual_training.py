from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import torch
from safetensors.torch import load_file

from alice.contracts.actuation import ActuatorTarget
from alice.contracts.motion import TargetUpdate
from alice.models.package import ResidualTrainingRecord
from alice.models.residual_state_space import (
    ResidualStateSpace,
    ResidualStateSpaceConfig,
)
from alice.motion.controller_response import (
    ActuatorResponseParameters,
    ControllerLimitMode,
    ControllerResponse,
    ControllerResponseConfig,
    ControllerState,
)
from alice.training.residual import (
    ResidualDataset,
    ResidualLossWeights,
    ResidualTrainingConfig,
    _response_step,
    recurrent_rollout,
    residual_objective_terms,
    train_residual,
)


def _model_config() -> ResidualStateSpaceConfig:
    return ResidualStateSpaceConfig(
        schema_version="residual-state-space-config/v1",
        model_id="tiny-residual-v1",
        affect_schema_id="affect-vector/v1",
        affect_dimensions=("valence", "arousal"),
        controller_response_model_id="controller-response-test-v1",
        calibration_sha256="a" * 64,
        controller_settings_sha256="b" * 64,
        actuator_names=("mouth_open", "neck_rotation"),
        hidden_size=4,
        residual_envelope=(0.08, 0.12),
        research_status="unfitted-research-prior",
        provenance="Hand-authored tiny-fixture priors; not empirical evidence.",
    )


def _dataset(*, split: str = "train", steps: int = 4) -> ResidualDataset:
    affect = torch.tensor(
        [
            [[0.1, 0.2], [0.2, 0.1], [0.3, 0.0], [0.2, -0.1]],
            [[-0.2, 0.3], [-0.1, 0.2], [0.0, 0.1], [0.1, 0.0]],
        ],
        dtype=torch.float32,
    )
    intensity = torch.tensor(
        [[[0.5], [0.6], [0.7], [0.6]], [[0.4], [0.5], [0.6], [0.5]]],
        dtype=torch.float32,
    )
    anchor = torch.tensor(
        [
            [[0.0, 0.1], [0.01, 0.1], [0.02, 0.09], [0.01, 0.08]],
            [[0.0, -0.1], [-0.01, -0.09], [-0.02, -0.08], [-0.01, -0.07]],
        ],
        dtype=torch.float32,
    )
    target = torch.tensor(
        [
            [[0.0, 0.0], [0.01, -0.01], [0.02, -0.02], [0.01, -0.01]],
            [[0.0, 0.0], [-0.01, 0.01], [-0.02, 0.02], [-0.01, 0.01]],
        ],
        dtype=torch.float32,
    )
    return ResidualDataset(
        split=split,
        dataset_id="tiny-derived-fixture-v1",
        split_id=f"tiny-{split}-split-v1",
        input_data_reference="tests/training deterministic synthetic fixture",
        permitted_use="automated software verification only",
        affect=affect[:, :steps],
        intensity=intensity[:, :steps],
        anchor_pose=anchor[:, :steps],
        response_position=(anchor * 0.9)[:, :steps],
        response_velocity=torch.zeros_like(anchor)[:, :steps],
        elapsed_s=torch.full((2, steps, 1), 0.1),
        target_residual=target[:, :steps],
        boundary_residual=target[:, 0],
    )


def _training_config(path: Path, *, seed: int = 19) -> ResidualTrainingConfig:
    return ResidualTrainingConfig(
        model=_model_config(),
        seed=seed,
        epochs=2,
        learning_rate=0.01,
        rollout_steps=3,
        controller_response_config=_controller_config(),
        artifact_directory=path,
        disposition="keep",
        note="Tiny CPU reproducibility fixture; not evidence for model selection.",
        losses=ResidualLossWeights(
            reconstruction=1.0,
            multistep_rollout=0.4,
            anchor_drift=0.2,
            boundary_continuity=0.3,
            realized_velocity=0.1,
            realized_acceleration=0.05,
            realized_jerk=0.02,
        ),
    )


def _controller_config() -> ControllerResponseConfig:
    def actuator(
        name: str, velocity: float, acceleration: float
    ) -> ActuatorResponseParameters:
        return ActuatorResponseParameters(
            actuator_name=name,
            firmware_speed_setting=1,
            firmware_acceleration_setting=1,
            speed_mode=ControllerLimitMode.CONTROLLER_LIMITED,
            acceleration_mode=ControllerLimitMode.CONTROLLER_LIMITED,
            max_velocity_per_s=velocity,
            max_acceleration_per_s2=acceleration,
            provenance="Synthetic training response fixture.",
        )

    return ControllerResponseConfig(
        schema_version="controller-response-config/v1",
        model_id="controller-response-test-v1",
        hardware_id="synthetic-v1",
        calibration_sha256="a" * 64,
        model_kind="piecewise-acceleration-speed-limited",
        actuators=(
            actuator("mouth_open", 1.2, 4.0),
            actuator("neck_rotation", 0.4, 1.0),
        ),
        provenance="Synthetic training response fixture.",
    )


def test_controller_response_sha_is_canonical_and_order_sensitive() -> None:
    """Non-canonical JSON or reordered actuators would obscure response identity."""

    config = _controller_config()
    payload = config.model_dump(mode="json")
    reversed_keys = dict(reversed(tuple(payload.items())))
    round_tripped = ControllerResponseConfig.model_validate(reversed_keys)
    reordered = config.model_copy(
        update={"actuators": tuple(reversed(config.actuators))}
    )

    assert config.response_sha256 == (
        "d79c9c29d3d2f7bca6a39305b883d32ea44384f7492a15c7b1ef2e4a4d5e5f40"
    )
    assert round_tripped.response_sha256 == config.response_sha256
    assert reordered.response_sha256 != config.response_sha256


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("max_velocity_per_s", 0.2),
        ("max_acceleration_per_s2", 0.5),
    ),
)
def test_controller_response_sha_binds_each_fitted_response_parameter(
    field: str,
    value: float,
) -> None:
    """Changing fitted dynamics must not retain the exact response identity."""

    config = _controller_config()
    changed_actuator = config.actuators[1].model_copy(update={field: value})
    changed = config.model_copy(
        update={"actuators": (*config.actuators[:-1], changed_actuator)}
    )

    assert changed.controller_settings_sha256 == config.controller_settings_sha256
    assert changed.response_sha256 != config.response_sha256


def test_fixed_seed_training_repeats_on_cpu(tmp_path: Path) -> None:
    """Using global or accelerator RNG state would change the saved weights."""

    first = train_residual(_training_config(tmp_path / "first"), _dataset())
    second = train_residual(_training_config(tmp_path / "second"), _dataset())

    assert first.weights_sha256 == second.weights_sha256
    first_weights = load_file(first.weights_path, device="cpu")
    second_weights = load_file(second.weights_path, device="cpu")
    assert first_weights.keys() == second_weights.keys()
    assert all(
        torch.equal(first_weights[name], second_weights[name]) for name in first_weights
    )


def test_training_restores_cpu_rng_and_warn_only_determinism(tmp_path: Path) -> None:
    """An offline run must not consume caller RNG or strengthen warn-only mode."""

    original_rng = torch.random.get_rng_state()
    original_mode = torch.get_deterministic_debug_mode()
    try:
        torch.random.default_generator.manual_seed(20260907)
        expected_rng = torch.random.get_rng_state().clone()
        torch.set_deterministic_debug_mode("warn")

        train_residual(_training_config(tmp_path / "restoration"), _dataset())

        assert torch.equal(torch.random.get_rng_state(), expected_rng)
        assert torch.get_deterministic_debug_mode() == 1
    finally:
        torch.random.set_rng_state(original_rng)
        torch.set_deterministic_debug_mode(original_mode)


def test_training_refuses_test_split_before_writing(tmp_path: Path) -> None:
    """A held-out test partition must never become optimizer input."""

    artifact_directory = tmp_path / "forbidden"
    with pytest.raises(ValueError, match="test split"):
        train_residual(
            _training_config(artifact_directory),
            _dataset(split="test"),
        )

    assert not artifact_directory.exists()


@pytest.mark.parametrize(
    ("loss_name", "steps", "minimum"),
    (
        ("realized_velocity", 1, 2),
        ("realized_acceleration", 2, 3),
        ("realized_jerk", 3, 4),
    ),
)
def test_enabled_derivative_loss_requires_enough_positions(
    tmp_path: Path,
    loss_name: str,
    steps: int,
    minimum: int,
) -> None:
    """An enabled derivative must never disappear as a silent zero scalar."""

    values = {
        "reconstruction": 0.0,
        "multistep_rollout": 0.0,
        "anchor_drift": 0.0,
        "boundary_continuity": 0.0,
        "realized_velocity": 0.0,
        "realized_acceleration": 0.0,
        "realized_jerk": 0.0,
    }
    values[loss_name] = 1.0
    config = replace(
        _training_config(tmp_path / loss_name),
        losses=ResidualLossWeights(**values),
    )

    with pytest.raises(
        ValueError,
        match=rf"{loss_name} loss requires at least {minimum} positions",
    ):
        train_residual(config, _dataset(steps=steps))


def test_zero_weight_derivatives_allow_single_position(tmp_path: Path) -> None:
    """Disabled derivative objectives must not impose unused sequence lengths."""

    config = replace(
        _training_config(tmp_path / "single"),
        rollout_steps=2,
        losses=ResidualLossWeights(
            reconstruction=1.0,
            multistep_rollout=0.0,
            anchor_drift=0.0,
            boundary_continuity=0.0,
            realized_velocity=0.0,
            realized_acceleration=0.0,
            realized_jerk=0.0,
        ),
    )

    result = train_residual(config, _dataset(steps=1))

    assert result.weights_path.is_file()


def test_enabled_multistep_rollout_requires_configured_horizon(
    tmp_path: Path,
) -> None:
    """A requested three-step rollout must not silently truncate to two steps."""

    config = replace(
        _training_config(tmp_path / "short-rollout"),
        losses=ResidualLossWeights(
            reconstruction=0.0,
            multistep_rollout=1.0,
            anchor_drift=0.0,
            boundary_continuity=0.0,
            realized_velocity=0.0,
            realized_acceleration=0.0,
            realized_jerk=0.0,
        ),
    )

    with pytest.raises(
        ValueError,
        match="multistep_rollout loss requires at least 3 positions",
    ):
        train_residual(config, _dataset(steps=2))


def test_recurrent_rollout_feeds_predicted_response_into_later_steps() -> None:
    """Teacher-forcing the recorded response would hide first-step state errors."""

    model_config = _model_config().model_copy(update={"hidden_size": 1})
    model = ResidualStateSpace(model_config)
    response_position_index = (
        len(model_config.affect_dimensions) + 1 + len(model_config.actuator_names)
    )
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.gru.bias_ih_l0[1] = -100.0
        model.gru.weight_ih_l0[2, response_position_index + 1] = 5.0
        model.residual_head.weight[1, 0] = 4.0
    dataset = _dataset(steps=2)

    rollout = recurrent_rollout(
        model,
        dataset,
        steps=2,
        controller_response_config=_controller_config(),
    )
    first_features = model.compose_features(
        affect=dataset.affect[:, :1],
        intensity=dataset.intensity[:, :1],
        anchor_pose=dataset.anchor_pose[:, :1],
        response_position=dataset.response_position[:, :1],
        response_velocity=dataset.response_velocity[:, :1],
        elapsed_s=dataset.elapsed_s[:, :1],
    )
    _, first_hidden = model(first_features, hidden=None)
    teacher_second_features = model.compose_features(
        affect=dataset.affect[:, 1:2],
        intensity=dataset.intensity[:, 1:2],
        anchor_pose=dataset.anchor_pose[:, 1:2],
        response_position=dataset.response_position[:, 1:2],
        response_velocity=dataset.response_velocity[:, 1:2],
        elapsed_s=dataset.elapsed_s[:, 1:2],
    )
    teacher_second, _ = model(teacher_second_features, hidden=first_hidden)
    # The per-axis 1.0/s² acceleration setting permits 0.01 normalized travel
    # during this first 0.1 s step; no invented global response rate is used.
    expected_first_neck_position = torch.tensor(0.1)

    assert float(rollout.realized_position[0, 0, 1].detach()) == pytest.approx(
        float(expected_first_neck_position.detach())
    )
    assert not torch.allclose(rollout.residuals[:, 1], teacher_second[:, 0])


def test_objective_terms_match_hand_derived_motion_errors() -> None:
    """Each loss must measure its named physical or boundary behavior."""

    prediction = torch.tensor([[[1.0], [2.0], [4.0], [8.0]]])
    target_residual = torch.zeros_like(prediction)
    predicted_realized = torch.tensor([[[0.0], [1.0], [4.0], [10.0]]])
    target_realized = torch.zeros_like(predicted_realized)

    terms = residual_objective_terms(
        prediction=prediction,
        target_residual=target_residual,
        anchor_pose=torch.zeros_like(prediction),
        boundary_residual=torch.tensor([[0.0]]),
        predicted_realized=predicted_realized,
        target_realized=target_realized,
        elapsed_s=torch.ones((1, 4, 1)),
        rollout_steps=3,
    )

    assert float(terms["reconstruction"]) == pytest.approx(85.0 / 4.0)
    assert float(terms["multistep_rollout"]) == pytest.approx(17.0 / 3.0)
    assert float(terms["anchor_drift"]) == pytest.approx(59.0 / 4.0)
    assert float(terms["boundary_continuity"]) == pytest.approx(1.0)
    assert float(terms["realized_velocity"]) == pytest.approx(46.0 / 3.0)
    assert float(terms["realized_acceleration"]) == pytest.approx(13.0 / 2.0)
    assert float(terms["realized_jerk"]) == pytest.approx(1.0)


def test_training_records_all_objectives_and_compact_provenance(
    tmp_path: Path,
) -> None:
    """Omitting a required objective or provenance field would make the run opaque."""

    result = train_residual(_training_config(tmp_path / "record"), _dataset())
    record = json.loads(result.research_record_path.read_text(encoding="utf-8"))

    assert result.weights_path.suffix == ".safetensors"
    assert result.weights_path.is_file()
    assert record["schema_version"] == "residual-training-record/v3"
    assert record["training"]["response_backend"] == {
        "schema_version": "training-response-backend/v1",
        "backend_id": "bounded-euler-surrogate/v1",
        "source_controller_response_sha256": _controller_config().response_sha256,
        "actuators": [
            {
                "actuator_name": "mouth_open",
                "max_velocity_per_s": 1.2,
                "max_acceleration_per_s2": 4.0,
            },
            {
                "actuator_name": "neck_rotation",
                "max_velocity_per_s": 0.4,
                "max_acceleration_per_s2": 1.0,
            },
        ],
    }
    assert (
        ResidualTrainingRecord.model_validate(record).model_dump(mode="json") == record
    )
    assert record["training"]["controller_response_sha256"] == (
        _controller_config().response_sha256
    )
    assert record["training"]["controller_settings_sha256"] == (
        _controller_config().controller_settings_sha256
    )
    assert (
        record["training"]["controller_response_sha256"]
        != record["training"]["controller_settings_sha256"]
    )
    assert record["artifact"]["weights_sha256"] == result.weights_sha256
    assert record["model"]["research_status"] == "unfitted-research-prior"
    assert record["model"]["provenance"] == (
        "Hand-authored tiny-fixture priors; not empirical evidence."
    )
    assert record["run"] == {
        "seed": 19,
        "device": "cpu",
        "disposition": "keep",
        "note": "Tiny CPU reproducibility fixture; not evidence for model selection.",
    }
    assert record["dataset"] == {
        "dataset_id": "tiny-derived-fixture-v1",
        "split": "train",
        "split_id": "tiny-train-split-v1",
        "input_data_reference": "tests/training deterministic synthetic fixture",
        "permitted_use": "automated software verification only",
    }
    required = {
        "reconstruction",
        "multistep_rollout",
        "anchor_drift",
        "boundary_continuity",
        "realized_velocity",
        "realized_acceleration",
        "realized_jerk",
        "total",
    }
    assert set(record["epochs"][-1]["losses"]) == required
    assert all(value >= 0.0 for value in record["epochs"][-1]["losses"].values())


def test_model_and_trainer_import_without_hardware_modules() -> None:
    """Offline ML imports must not initialize or even load hardware adapters."""

    code = """
import sys
import alice.models.residual_state_space
import alice.training.residual
assert not any(name.startswith('alice.hardware') for name in sys.modules)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_surrogate_differs_from_reference_and_is_partition_sensitive() -> None:
    """Resolved limits do not imply reference dynamics or partition invariance."""

    config = _controller_config()
    command = torch.full((1, 2), 0.5, dtype=torch.float64)
    initial = torch.zeros_like(command)
    position, velocity = _response_step(
        command=command,
        position=initial,
        velocity=initial,
        elapsed_s=torch.tensor([[0.2]], dtype=torch.float64),
        controller_response_config=config,
    )
    reference = ControllerResponse(config=config)
    predicted = [
        reference.predict(
            ControllerState(
                schema_version="controller-state/v1",
                actuator_name=a.actuator_name,
                calibration_sha256=config.calibration_sha256,
                position=0.0,
                velocity=0.0,
            ),
            TargetUpdate(
                offset_s=0.0,
                targets=(
                    ActuatorTarget(
                        actuator_name=a.actuator_name, normalized_position=0.5
                    ),
                ),
            ),
            elapsed_s=0.2,
        )
        for a in config.actuators
    ]
    assert position.tolist()[0] == pytest.approx([0.16, 0.04])
    assert [state.position for state in predicted] == pytest.approx([0.08, 0.02])
    assert velocity.tolist()[0] == pytest.approx(
        [state.velocity for state in predicted]
    )
    partitioned_position, partitioned_velocity = initial, initial
    for _ in range(2):
        partitioned_position, partitioned_velocity = _response_step(
            command=command,
            position=partitioned_position,
            velocity=partitioned_velocity,
            elapsed_s=torch.tensor([[0.1]], dtype=torch.float64),
            controller_response_config=config,
        )
    assert partitioned_position.tolist()[0] == pytest.approx([0.12, 0.03])
    assert not torch.allclose(partitioned_position, position)


def test_bounded_euler_surrogate_preserves_finite_nonzero_command_gradients() -> None:
    command = torch.tensor(
        [[0.03125, 0.015625]], dtype=torch.float64, requires_grad=True
    )
    initial = torch.zeros_like(command)
    position, velocity = _response_step(
        command=command,
        position=initial,
        velocity=initial,
        elapsed_s=torch.tensor([[0.25]], dtype=torch.float64),
        controller_response_config=_controller_config(),
    )
    assert position.dtype == velocity.dtype == command.dtype
    assert position.device == velocity.device == command.device
    assert position.grad_fn is not None and velocity.grad_fn is not None
    (position.square().sum() + velocity.square().sum()).backward()
    assert command.grad is not None
    assert torch.isfinite(command.grad).all()
    assert torch.all(command.grad != 0)
    torch.testing.assert_close(command.grad, 2 * command)
