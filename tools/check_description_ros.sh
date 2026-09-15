#!/usr/bin/env bash
# Run inside the description container, with the repository read-only at /repo.
set -euo pipefail
mkdir -p /tmp/alice-description-ws
cd /tmp/alice-description-ws
# ROS setup scripts can reference unset shell variables.
set +u
source /opt/ros/jazzy/setup.bash
set -u
colcon build --base-paths /repo/src/alice_description --packages-select alice_description
set +u
source install/setup.bash
set -u
python3 -m pytest -p no:cacheprovider /repo/tests/test_robot_description.py /repo/tests/test_robot_export.py -q
python3 /repo/tools/export_robot_preview.py --output /tmp/alice-description-export
check_urdf /tmp/alice-description-export/alice.urdf
ros2 launch alice_description display.launch.py gui:=false rviz:=false > /tmp/alice-preview-launch.log 2>&1 &
preview_pid=$!
cleanup() {
    # Background shell jobs may inherit SIGINT ignored. SIGTERM is handled by
    # ROS launch and shuts down its child nodes as well.
    kill -TERM "$preview_pid" 2>/dev/null || true
    wait "$preview_pid" 2>/dev/null || true
    cat /tmp/alice-preview-launch.log
}
trap cleanup EXIT
python3 /repo/tools/ros_description_smoke.py
if ros2 launch alice_description display.launch.py height_m:=nan gui:=false rviz:=false > /tmp/alice-invalid-height.log 2>&1; then
    cat /tmp/alice-invalid-height.log
    exit 1
fi
python3 - <<'PY'
from pathlib import Path
assert 'height_m must be a finite value' in Path('/tmp/alice-invalid-height.log').read_text()
print('PASS: invalid launch height is rejected before nodes start.')
PY
