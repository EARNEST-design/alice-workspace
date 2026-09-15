#!/usr/bin/env bash
set -eo pipefail

# ROS-generated setup scripts are not compatible with Bash nounset.
source /opt/ros/lyrical/setup.bash
if [[ -f /opt/alice/ros/install/setup.bash ]]; then
    source /opt/alice/ros/install/setup.bash
fi

exec "$@"
