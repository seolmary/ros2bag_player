#!/usr/bin/env bash
# Launch the bag_player GUI + RViz for a bag (log) folder.
#
# Usage:
#   ./run.sh                                   # pick a bag in the selection UI
#   ./run.sh /path/to/bag_records              # skip the UI, play that bag
#   ./run.sh --pick [SEARCH_ROOT]              # force the UI (optionally elsewhere)
#   ./run.sh --last                            # replay the last picked bag
#   ./run.sh /path/to/bag_records rviz:=false storage:=sqlite3
#
# Any `name:=value` argument is forwarded to `ros2 launch` untouched, so
# `./run.sh rviz:=false` still opens the picker.
set -euo pipefail

# --- defaults ---------------------------------------------------------------
DEFAULT_BAG="/home/gs-omen/ros2bag/bag_records_0707_elevation_map/bag_records"
BAG_SEARCH_ROOT="${BAG_SEARCH_ROOT:-$HOME/ros2bag}"
ROS_DISTRO_SETUP="/opt/ros/jazzy/setup.bash"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAST_BAG_FILE="${XDG_CACHE_HOME:-$HOME/.cache}/bag_player/last_bag"

# --- parse the leading argument ---------------------------------------------
# Anything that is not a bag path (missing, an option, or a launch `a:=b` pair)
# means "no bag given" -> open the picker.
PICK=1
BAG=""
STORAGE=""

case "${1-}" in
  -h|--help)
    # Print the header comment block (everything above `set -euo pipefail`).
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' \
        "${BASH_SOURCE[0]}"
    exit 0
    ;;
  -p|--pick)
    shift
    if [[ -n "${1-}" && "$1" != *:=* && "$1" != -* ]]; then
      BAG_SEARCH_ROOT="$1"
      shift
    fi
    ;;
  -l|--last)
    shift
    if [[ -s "$LAST_BAG_FILE" ]]; then
      IFS=$'\t' read -r BAG STORAGE < "$LAST_BAG_FILE"
      PICK=0
    else
      echo "[run.sh] no remembered bag yet — opening the picker." >&2
    fi
    ;;
  ""|*:=*)
    ;;                       # no bag path -> picker (launch args stay in "$@")
  *)
    BAG="$1"
    PICK=0
    shift
    ;;
esac

# --- source environments ----------------------------------------------------
# ROS/ament setup scripts reference unset variables, so relax `nounset` while
# sourcing them, then restore it. (Sourced before the picker: it is imported
# from the bag_player package.)
set +u
# shellcheck disable=SC1090
source "$ROS_DISTRO_SETUP"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/install/setup.bash"
set -u

# --- bag selection UI -------------------------------------------------------
if [[ "$PICK" -eq 1 ]]; then
  INITIAL="$DEFAULT_BAG"
  if [[ -s "$LAST_BAG_FILE" ]]; then
    IFS=$'\t' read -r INITIAL _ < "$LAST_BAG_FILE"
  fi

  SEL_FILE="$(mktemp)"
  # `exec` at the end replaces this shell, so clean up as soon as we are done
  # with the file rather than relying only on the EXIT trap.
  trap 'rm -f "$SEL_FILE"' EXIT
  if ! python3 -m bag_player.bag_picker \
        --root "$BAG_SEARCH_ROOT" --initial "$INITIAL" --out "$SEL_FILE" \
        > /dev/null; then
    echo "[run.sh] no bag selected — aborting." >&2
    exit 1
  fi
  IFS=$'\t' read -r BAG STORAGE < "$SEL_FILE"
  rm -f "$SEL_FILE"
  trap - EXIT
fi

# --- validate ---------------------------------------------------------------
BAG="${BAG/#\~/$HOME}"
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

# --- storage id -------------------------------------------------------------
# Read it from the bag itself unless the caller passed `storage:=...`.
EXTRA=()
if [[ ! " $* " == *" storage:="* ]]; then
  if [[ -z "$STORAGE" ]]; then
    STORAGE="$(awk -F': *' '/^[[:space:]]*storage_identifier:/ {
                 gsub(/["\r]/, "", $2); print $2; exit }' \
               "$BAG/metadata.yaml" || true)"
  fi
  [[ -n "$STORAGE" ]] && EXTRA+=("storage:=$STORAGE")
fi

# --- remember the choice for `--last` / the next picker default --------------
mkdir -p "$(dirname "$LAST_BAG_FILE")"
printf '%s\t%s\n' "$BAG" "$STORAGE" > "$LAST_BAG_FILE"

# --- launch -----------------------------------------------------------------
echo "[run.sh] bag: $BAG${STORAGE:+  (storage: $STORAGE)}"
exec ros2 launch bag_player player.launch.py bag:="$BAG" \
     ${EXTRA[@]+"${EXTRA[@]}"} "$@"
