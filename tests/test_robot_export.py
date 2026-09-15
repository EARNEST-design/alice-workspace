"""Export contract: same URDF tree, portable artifact and invalid scale rejection."""
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]


def export(tmp_path, height="0.62"):
    script = ROOT / "tools/export_robot_preview.py"
    assert script.exists(), "Preview exporter has not been implemented"
    return subprocess.run([sys.executable, str(script), "--output", str(tmp_path), "--height-m", height],
                          capture_output=True, text=True)


def test_export_preserves_urdf_tree_and_geometry(tmp_path):
    result = export(tmp_path)
    assert result.returncode == 0, result.stderr
    urdf = ET.parse(tmp_path / "alice.urdf").getroot()
    data = json.loads((tmp_path / "model.json").read_text())
    assert {l["name"] for l in data["links"]} == {l.get("name") for l in urdf.findall("link")}
    assert {j["name"] for j in data["joints"]} == {j.get("name") for j in urdf.findall("joint")}
    assert sum(len(l["visuals"]) for l in data["links"]) == len(urdf.findall("link/visual"))
    assert data["height_m"] == .62
    assert (tmp_path / "index.html").exists()


def test_scaled_export_records_active_height_separately_from_reference(tmp_path):
    result = export(tmp_path, "1.24")
    assert result.returncode == 0, result.stderr
    data = json.loads((tmp_path / "model.json").read_text())
    assert data["height_m"] == data["register"]["preview_height_m"] == 1.24
    assert data["register"]["reference_height_m"] == .62


@pytest.mark.parametrize("height", ["-1", "0", "nan", "inf", "3.1"])
def test_invalid_scale_fails_before_creating_output(tmp_path, height):
    result = export(tmp_path, height)
    assert result.returncode != 0
    assert "height" in result.stderr.lower()
    assert not (tmp_path / "alice.urdf").exists()
