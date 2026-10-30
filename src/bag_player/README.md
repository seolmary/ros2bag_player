# bag_player

A **video-player style controller for ROS 2 bags**. Unlike `ros2 bag play`, this
lets you treat a recording like a video: play, pause, **rewind / reverse**,
**fast-forward**, change speed, **click anywhere on the timeline to jump**, and
step frame-by-frame.

It works by reading the bag with `rosbag2_py.SequentialReader` and republishing
messages on their original topics, driven by a virtual *bag clock*. The clock is
published on `/clock`, so any consumer running with `use_sim_time:=true`
(e.g. RViz2) follows the same time — including jumps and reverse motion.

True reverse playback is real (not just stepping): the reader uses
`ReadOrder(reverse=True)`, so messages stream out in decreasing-timestamp order.

## Features

| Control | How |
|---|---|
| Play / Pause | `▶ / ⏸` button or **Space** |
| Reverse / Fast-forward | Speed buttons `-8x … 8x` (negative = reverse) or **↑/↓** |
| Jump to a point | **Click** anywhere on the timeline, or drag to scrub |
| Frame step | `⏪ / ⏩` buttons or **←/→** — one message of the selected topic (default: busiest PointCloud2), pauses |
| Jump to start / end | `⏮ / ⏭` or **Home/End** |
| Loop | Loop checkbox |
| Show/hide a topic | Per-topic checkboxes (disable heavy topics like point clouds) |

## Build

```bash
cd /home/gs-omen/Codes/ros2bag_player
source /opt/ros/jazzy/setup.bash
colcon build --packages-select bag_player
source install/setup.bash
```

The control panel uses PyQt5; qtawesome supplies the button icons (optional,
unicode glyphs otherwise):

```bash
sudo apt install python3-pyqt5 python3-qtawesome
```

## Run

Easiest — `run.sh` sources the workspace, lets you **pick which bag (log) folder
to play** in a dialog, then launches the player **and** RViz2 (with sim time):

```bash
./run.sh                       # pick a bag under ~/ros2bag
./run.sh --pick /other/root    # scan a different folder
./run.sh --last                # replay the bag picked last time
./run.sh /path/to/bag_records  # skip the dialog
```

Desktop launcher (app menu + desktop icon, runs `run.sh` via the login shell so
`ROS_DOMAIN_ID` etc. match your terminal): `tools/install_launcher.sh`
(`--uninstall` to remove). Right-click the icon for "Replay last bag".

The picker lists every directory containing a `metadata.yaml` (up to 4 levels
below the search root) with duration, message/topic counts, size and record
time; `Browse folder…` opens any other location. The storage id (`mcap` /
`sqlite3`) is read from the selected bag, so `.db3` bags need no extra flag.
Set `BAG_SEARCH_ROOT` to change the default search root.

Or drive the launch file directly:

```bash
ros2 launch bag_player player.launch.py bag:=/home/gs-omen/ros2bag/bag_records
```

Player only (visualize however you like), or without RViz:

```bash
ros2 run bag_player bag_player --bag /home/gs-omen/ros2bag/bag_records
# omit --bag to choose the bag in the same dialog:
ros2 run bag_player bag_player --bag-root ~/ros2bag
# or:  ros2 launch bag_player player.launch.py bag:=/path/to/bag rviz:=false
```

The picker can also be run on its own — it prints `<path><TAB><storage_id>`:

```bash
ros2 run bag_player bag_picker --root ~/ros2bag
```

For old `.db3` bags use `--storage sqlite3` (or `storage:=sqlite3` in the launch).

### Using your own RViz / other tools

The player publishes `/clock`. Anything that should follow the playhead (RViz2,
your nodes) must run with sim time:

```bash
rviz2 --ros-args -p use_sim_time:=true
```

Then add displays for the topics you care about.

The launch file builds the RViz display list from the bag itself: every
recorded topic with a known display class (point clouds, grid maps, markers,
odometry, images, ...) gets a display, and saved displays whose topic is not in
the bag are dropped. `rviz/bag_player.rviz` is only the seed; the generated
file is `~/.cache/bag_player/rviz/<bag name>.rviz`, one per bag, and RViz saves
back into it (Ctrl+S) so per-bag layout tweaks survive the next launch. Delete
that file to start over from the seed. `rviz_config:=` seeds from that file
instead.

Compressed depth images (`/.../depth/compressed`, 16-bit PNG) are relabelled
`16UC1; png` on the way out so RViz's compressed transport decodes them as
depth instead of an 8-bit image with the wrong stride.

## Notes & limits

- **Fixed Frame** is `map` (the bag's `map → odom → base_link` tree).
- During fast playback the engine self-throttles: it advances the clock by wall
  time regardless, so if it can't publish fast enough it drops messages rather
  than falling behind. Per-tick caps live at the top of `player_core.py`.
- The `▶` / Space button **always resumes forward**. Reverse is only entered
  deliberately with the negative speed buttons (or ↓), so pausing during reverse
  and pressing play won't surprise you by continuing backward.
- **Reverse playback / backward seeks move sim time backward.** RViz responds by
  logging `Detected jump back in time. Clearing TF buffer / Resetting RViz` and
  resetting every display. Unthrottled (one backward step per ~2 ms tick) this
  broke RViz rendering — robot and grid maps turned permanently flat white — so
  backward `/clock` steps are capped at one per `BACKWARD_CLOCK_PERIOD_S`
  (0.25 s wall). Between steps RViz's clock stays slightly ahead of the replayed
  stamps, which TF tolerates. For a mapping/SLAM consumer that
  needs faithful, monotonic time, **play forward at 1x**.
- `/tf_static` is latched once at startup so frames stay resolvable after seeks.

## Layout

```
bag_player/
  bag_player/player_core.py    # reader + publishers + playback engine (rclpy)
  bag_player/control_panel.py  # PyQt5 timeline / transport / speed UI
  bag_player/bag_picker.py     # startup dialog: choose which bag folder to play
  bag_player/app.py            # entry point: wires engine + Qt together
  launch/player.launch.py      # player (+ RViz2 with use_sim_time)
  rviz/bag_player.rviz         # seed RViz2 config (displays regenerated per bag)
```
