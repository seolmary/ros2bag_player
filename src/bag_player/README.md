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
| Frame step | `⏪ / ⏩` buttons or **←/→** (0.1 s steps, while paused) |
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

## Run

Easiest — launch the player **and** RViz2 (with sim time) together:

```bash
ros2 launch bag_player player.launch.py bag:=/home/gs-omen/ros2bag/bag_records
```

Player only (visualize however you like), or without RViz:

```bash
ros2 run bag_player bag_player --bag /home/gs-omen/ros2bag/bag_records
# or:  ros2 launch bag_player player.launch.py bag:=/path/to/bag rviz:=false
```

For old `.db3` bags use `--storage sqlite3` (or `storage:=sqlite3` in the launch).

### Using your own RViz / other tools

The player publishes `/clock`. Anything that should follow the playhead (RViz2,
your nodes) must run with sim time:

```bash
rviz2 --ros-args -p use_sim_time:=true
```

Then add displays for the topics you care about (this bag: `/ouster/points`,
`/elevation_map`, `/odom`, `/feet_visualize`, TF). The bundled RViz config
(`rviz/bag_player.rviz`) is already set up for them with `Fixed Frame: map`.

## Notes & limits

- **Fixed Frame** is `map` (the bag's `map → odom → base_link` tree).
- During fast playback the engine self-throttles: it advances the clock by wall
  time regardless, so if it can't publish fast enough it drops messages rather
  than falling behind. Per-tick caps live at the top of `player_core.py`.
- The `▶` / Space button **always resumes forward**. Reverse is only entered
  deliberately with the negative speed buttons (or ↓), so pausing during reverse
  and pressing play won't surprise you by continuing backward.
- **Reverse playback / backward seeks move sim time backward.** RViz responds by
  logging `Detected jump back in time. Clearing TF buffer / Resetting RViz` on
  every frame and clearing its display. This is inherent to RViz under
  `use_sim_time` — it is expected, not a crash. For a mapping/SLAM consumer that
  needs faithful, monotonic time, **play forward at 1x**.
- `/tf_static` is latched once at startup so frames stay resolvable after seeks.

## Layout

```
bag_player/
  bag_player/player_core.py    # reader + publishers + playback engine (rclpy)
  bag_player/control_panel.py  # PyQt5 timeline / transport / speed UI
  bag_player/app.py            # entry point: wires engine + Qt together
  launch/player.launch.py      # player (+ RViz2 with use_sim_time)
  rviz/bag_player.rviz         # RViz2 config for this bag's topics
```
