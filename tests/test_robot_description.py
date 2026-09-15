"""Model contracts: catches broken topology, invented motor IDs and scale errors."""

import json
from pathlib import Path

import numpy as np
import pytest
import xacro
import yaml
from urdf_parser_py.urdf import URDF

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "ros2_ws/src/alice_description"


def model(height="0.62"):
    path = PACKAGE / "urdf/alice.urdf.xacro"
    assert path.exists(), "The ROS robot description has not been implemented"
    return URDF.from_xml_string(
        xacro.process_file(str(path), mappings={"height_m": height}).toxml()
    )


def transforms(robot):
    poses = {robot.get_root(): np.zeros(3)}
    remaining = list(robot.joints)
    while remaining:
        ready = [j for j in remaining if j.parent in poses]
        assert ready, "Disconnected or cyclic tree"
        for joint in ready:
            # Neutral model uses aligned joint frames, so translations suffice.
            assert joint.origin.rpy == [0.0, 0.0, 0.0]
            poses[joint.child] = poses[joint.parent] + np.array(joint.origin.xyz)
            remaining.remove(joint)
    return poses


def test_tree_covers_all_links_without_duplicate_names():
    robot = model()
    assert robot.get_root() == "base_link"
    assert len({link.name for link in robot.links}) == len(robot.links)
    assert len({j.name for j in robot.joints}) == len(robot.joints)
    assert len(robot.joints) == len(robot.links) - 1
    assert set(transforms(robot)) == set(robot.link_map)


def test_motor_map_preserves_observed_channels_and_excludes_disconnected_output():
    robot = model()
    mapping = json.loads((PACKAGE / "config/motor_map.json").read_text())
    known = {m["channel"]: m["joint"] for m in mapping["head_actuators"]}
    assert known == {
        0: "neck_rotation",
        1: "head_tilt",
        2: "face_pitch",
        3: "lower_eyelids",
        4: "upper_eyelids",
        5: "forehead_frown",
        6: "mouth_open",
        8: "right_eye_horizontal",
        9: "left_mouth_corner",
        10: "left_eye_horizontal",
        11: "right_mouth_corner",
    }
    assert len(mapping["head_actuators"]) == 11
    for record in mapping["head_actuators"]:
        assert record["joint"] in robot.joint_map
        assert record["geometry_status"] == "unmeasured_proxy"
        assert record["pulse_to_si"] is None
    for record in mapping["body_actuators"]:
        assert record["joint"] in robot.joint_map
        assert record["channel"] is None and record["controller"] is None
        assert record["status"] == "hypothesis"
    mapped = {
        r["joint"]
        for group in ("head_actuators", "body_actuators")
        for r in mapping[group]
    }
    independent = {
        j.name for j in robot.joints if j.type != "fixed" and j.mimic is None
    }
    assert independent == mapped


def test_paired_eyelids_mimic_their_single_actuators():
    robot = model()
    followers = {j.name: j.mimic.joint for j in robot.joints if j.mimic}
    assert followers == {
        "right_lower_eyelid_mimic": "lower_eyelids",
        "right_upper_eyelid_mimic": "upper_eyelids",
    }


def test_visual_limits_are_finite_and_neutral_is_inside_range():
    for joint in model().joints:
        if joint.type == "fixed":
            continue
        assert np.isclose(np.linalg.norm(joint.axis), 1)
        assert np.all(
            np.isfinite(
                [
                    joint.limit.lower,
                    joint.limit.upper,
                    joint.limit.effort,
                    joint.limit.velocity,
                ]
            )
        )
        assert joint.limit.lower <= 0 <= joint.limit.upper
        assert joint.limit.lower < joint.limit.upper


def test_standing_soles_and_overall_height_match_assumed_scale():
    robot = model()
    poses = transforms(robot)
    for side in ("left", "right"):
        assert poses[side + "_sole"][2] == pytest.approx(0)
    assert poses["head_top"][2] == pytest.approx(0.62)
    assert poses["left_foot"][1] > 0 > poses["right_foot"][1]


def test_scaling_changes_lengths_but_not_angles():
    small, large = model(), model("1.24")
    for name, joint in small.joint_map.items():
        bigger = large.joint_map[name]
        assert np.allclose(np.array(joint.origin.xyz) * 2, bigger.origin.xyz)
        if joint.type == "revolute":
            for field in ("lower", "upper", "effort", "velocity"):
                assert getattr(joint.limit, field) == getattr(bigger.limit, field)
        if joint.type == "prismatic":
            assert bigger.limit.upper == pytest.approx(joint.limit.upper * 2)
    for a, b in zip(small.links, large.links):
        for va, vb in zip(a.visuals, b.visuals):
            ga, gb = va.geometry, vb.geometry
            if hasattr(ga, "size"):
                assert np.allclose(np.array(ga.size) * 2, gb.size)
            else:
                assert gb.radius == pytest.approx(ga.radius * 2)
                if hasattr(ga, "length"):
                    assert gb.length == pytest.approx(ga.length * 2)


def test_description_has_no_hardware_plugins_or_invented_inertia():
    robot = model()
    assert all(link.inertial is None for link in robot.links)
    assert not robot.transmissions


def test_rviz_resolves_all_urdf_links_to_the_preview_frame_namespace():
    config = yaml.safe_load((PACKAGE / "rviz/alice.rviz").read_text())
    display = next(
        d
        for d in config["Visualization Manager"]["Displays"]
        if d["Class"] == "rviz_default_plugins/RobotModel"
    )
    prefix = display.get("TF Prefix", "").strip("/")
    link_frames = {
        f"{prefix}/{link.name}" if prefix else link.name for link in model().links
    }
    published_frames = {f"alice_preview/{link.name}" for link in model().links}
    assert link_frames == published_frames
