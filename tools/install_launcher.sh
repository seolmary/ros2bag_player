#!/usr/bin/env bash
# Register a "ROS 2 Bag Player" launcher (app menu + desktop icon) that runs
# run.sh. Re-run after moving the repo. Remove with: --uninstall
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$HOME/.local/share/applications/bag_player.desktop"
DESK="${XDG_DESKTOP_DIR:-$HOME/Desktop}/bag_player.desktop"

if [[ "${1-}" == "--uninstall" ]]; then
  rm -f "$APP" "$DESK"
  update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
  echo "removed launcher"; exit 0
fi

mkdir -p "$(dirname "$APP")"
# `bash -ic` sources ~/.bashrc so the launcher sees the same ROS env
# (ROS_DOMAIN_ID etc.) as a terminal would.
cat > "$APP" <<DESKTOP
[Desktop Entry]
Type=Application
Name=ROS 2 Bag Player
Comment=Pick a rosbag2 folder and replay it with RViz
Exec=bash -ic $ROOT/run.sh
Icon=$ROOT/src/bag_player/icons/bag_player.svg
Terminal=false
Categories=Development;
Keywords=ros;rosbag;rviz;replay;
Actions=last;

[Desktop Action last]
Name=Replay last bag
Exec=bash -ic "$ROOT/run.sh --last"
DESKTOP
chmod +x "$APP"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

# Desktop icon: GNOME (ding) only launches .desktop files marked trusted.
if [[ -d "$(dirname "$DESK")" ]]; then
  cp "$APP" "$DESK"; chmod +x "$DESK"
  gio set "$DESK" metadata::trusted true 2>/dev/null || true
fi
echo "installed: $APP"
[[ -f "$DESK" ]] && echo "installed: $DESK"
