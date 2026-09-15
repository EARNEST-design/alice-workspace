#!/usr/bin/env python3
"""Expand Xacro once, then export those same shapes and joints for the viewer."""
import argparse
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import xacro

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/alice_description"


def numbers(value):
    return [float(x) for x in value.split()]


def origin(element):
    pose = element.find("origin")
    return {"xyz": numbers(pose.get("xyz", "0 0 0")) if pose is not None else [0, 0, 0],
            "rpy": numbers(pose.get("rpy", "0 0 0")) if pose is not None else [0, 0, 0]}


def preview_data(xml, height):
    robot = ET.fromstring(xml)
    materials = {m.get("name"): numbers(m.find("color").get("rgba")) for m in robot.findall("material")}
    links, joints = [], []
    for link in robot.findall("link"):
        visuals = []
        for visual in link.findall("visual"):
            geometry = list(visual.find("geometry"))[0]
            shape = {"type": geometry.tag}
            for key, value in geometry.attrib.items():
                shape[key] = numbers(value) if key == "size" else float(value)
            visuals.append({**origin(visual), "geometry": shape,
                            "color": materials[visual.find("material").get("name")]})
        links.append({"name": link.get("name"), "visuals": visuals})
    for joint in robot.findall("joint"):
        entry = {"name": joint.get("name"), "type": joint.get("type"), **origin(joint),
                 "parent": joint.find("parent").get("link"), "child": joint.find("child").get("link")}
        if entry["type"] != "fixed":
            entry["axis"] = numbers(joint.find("axis").get("xyz"))
            entry["limit"] = {k: float(v) for k, v in joint.find("limit").attrib.items()}
        mimic = joint.find("mimic")
        if mimic is not None:
            entry["mimic"] = {"joint": mimic.get("joint"), "multiplier": float(mimic.get("multiplier", "1")),
                              "offset": float(mimic.get("offset", "0"))}
        joints.append(entry)
    register = json.loads((PACKAGE / "config/motor_map.json").read_text())
    register["preview_height_m"] = height
    return {"name": robot.get("name"), "height_m": height, "links": links, "joints": joints,
            "register": register}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--height-m", type=float, default=.62)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/robot-description")
    args = parser.parse_args()
    if not math.isfinite(args.height_m) or not .1 <= args.height_m <= 3:
        parser.error("height must be finite and between 0.1 and 3.0 metres")
    xml = xacro.process_file(str(PACKAGE / "urdf/alice.urdf.xacro"),
                             mappings={"height_m": str(args.height_m)}).toprettyxml(indent="  ")
    data = preview_data(xml, args.height_m)
    template = (ROOT / "tools/robot_preview.html").read_text()
    encoded = json.dumps(data, separators=(",", ":"))
    # JSON lives inside a script element: escape any HTML-opening characters.
    html = template.replace("__ALICE_MODEL_JSON__", encoded.replace("<", "\\u003c"))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "alice.urdf").write_text(xml)
    (args.output / "model.json").write_text(json.dumps(data, indent=2) + "\n")
    (args.output / "index.html").write_text(html)
    print(f"Exported {len(data['links'])} links and {len(data['joints'])} joints to {args.output}")


if __name__ == "__main__":
    main()
