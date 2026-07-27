"""Entry point: start the playback engine and the Qt control panel.

The rclpy node (PlayerCore) runs its own play thread and publishes from it, so we
do not need a spinning executor — Qt owns the main thread. A lightweight executor
is still spun in the background so the node responds to parameter/shutdown events.
"""

import argparse
import os
import signal
import sys
import threading

import rclpy
from rclpy.executors import SingleThreadedExecutor

from bag_player.control_panel import ControlPanel
from bag_player.player_core import PlayerCore


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog='bag_player',
        description='Video-player style controller for a ROS 2 bag.')
    parser.add_argument('--bag', '-b', default=None,
                        help='Path to the bag directory (the folder with '
                             'metadata.yaml). Omit to pick one in a dialog.')
    parser.add_argument('--bag-root', default='~/ros2bag',
                        help='Where the selection dialog looks for bags '
                             '(default: ~/ros2bag).')
    parser.add_argument('--storage', '-s', default='mcap',
                        help='Storage id (default: mcap; use sqlite3 for .db3 bags).')
    # Drop ROS args injected by ros2 launch / run.
    known, _ = parser.parse_known_args(argv)
    return known


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    rclpy.init(args=None)
    args = _parse_args(argv)

    storage_id = args.storage
    if not args.bag:
        # No bag on the command line: let the user choose one.
        from bag_player.bag_picker import pick_with_gui

        chosen = pick_with_gui(args.bag_root)
        if chosen is None:
            print('[bag_player] No bag selected.', file=sys.stderr)
            rclpy.shutdown()
            return 1
        args.bag = chosen.path
        storage_id = chosen.storage_id

    bag_uri = os.path.abspath(os.path.expanduser(args.bag))
    if not os.path.isdir(bag_uri):
        print(f'[bag_player] Bag directory not found: {bag_uri}', file=sys.stderr)
        rclpy.shutdown()
        return 1

    try:
        player = PlayerCore(bag_uri, storage_id=storage_id)
    except Exception as exc:
        print(f'[bag_player] Failed to open bag: {exc}', file=sys.stderr)
        rclpy.shutdown()
        return 1

    # Spin the node in the background for liveliness / clean shutdown.
    executor = SingleThreadedExecutor()
    executor.add_node(player)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    # Qt application (main thread).
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    # Let Ctrl-C in the terminal close the GUI.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    panel = ControlPanel(player)
    panel.show()

    print(f'[bag_player] Playing {bag_uri}')
    print(f'[bag_player] {player.message_count} messages, '
          f'{player.duration_ns / 1e9:.1f} s, {len(player.topic_types)} topics')
    print('[bag_player] Publishing /clock — run RViz2 with use_sim_time:=true.')

    exit_code = app.exec_()

    # Clean teardown: stop the play thread, unblock the executor, then destroy.
    player.shutdown()
    executor.shutdown()
    spin_thread.join(timeout=2.0)
    player.destroy_node()
    if rclpy.ok():
        rclpy.shutdown()
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
