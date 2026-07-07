#!/usr/bin/env bash
# Launch the bag_player GUI + RViz for a given bag.
#
# Usage:
#   ./run.sh [BAG_PATH] [extra launch args...]
#
# Examples:
#   ./run.sh                                   # use default bag below
#   ./run.sh /path/to/bag_records             # custom bag
#   ./run.sh /path/to/bag_records robot_model:=false
#   ./run.sh /path/to/bag_records rviz:=false storage:=sqlite3
set -euo pipefail

# --- defaults ---------------------------------------------------------------
DEFAULT_BAG="/home/gs-omen/ros2bag/bag_records_0707_elevation_map/bag_records"
ROS_DISTRO_SETUP="/opt/ros/jazzy/setup.bash"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- resolve bag path -------------------------------------------------------
BAG="${1:-$DEFAULT_BAG}"
shift || true   # remaining args ($@) are passed straight to ros2 launch

if [[ ! -e "$BAG/metadata.yaml" ]]; then
  echo "[run.sh] ERROR: no rosbag2 bag at: $BAG" >&2
  echo "[run.sh]        (expected a 'metadata.yaml' inside that directory)" >&2
  # Helpful hint: if a metadata.yaml lives one level deeper, point it out.
  nested="$(find "$BAG" -maxdepth 2 -name metadata.yaml 2>/dev/null | head -1 || true)"
  if [[ -n "$nested" ]]; then
    echo "[run.sh] HINT: did you mean: $(dirname "$nested")" >&2
  fi
  exit 1
fi

# --- source environments ----------------------------------------------------
# ROS/ament setup scripts reference unset variables, so relax `nounset` while
# sourcing them, then restore it.
set +u
# shellcheck disable=SC1090
source "$ROS_DISTRO_SETUP"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/install/setup.bash"
set -u

# --- launch -----------------------------------------------------------------
echo "[run.sh] bag: $BAG"
exec ros2 launch bag_player player.launch.py bag:="$BAG" "$@"
