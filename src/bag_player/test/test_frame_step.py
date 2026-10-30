"""Frame step lands exactly on consecutive messages of the frame topic, both directions."""
import os
import time

import pytest

BAG = os.path.expanduser('~/ros2bag/bag_records_20260918/bag_records_20260918_121338')


@pytest.mark.skipif(not os.path.isdir(BAG), reason='sample bag not present')
def test_step_frame():
    import rclpy
    import rosbag2_py
    from bag_player.player_core import PlayerCore

    r = rosbag2_py.SequentialReader()
    r.open(rosbag2_py.StorageOptions(uri=BAG, storage_id='mcap'), rosbag2_py.ConverterOptions('', ''))
    r.set_filter(rosbag2_py.StorageFilter(topics=['/raw_points']))
    stamps = [r.read_next()[2] for _ in range(4)]

    rclpy.init()
    p = PlayerCore(BAG)
    try:
        def step(d):
            p.step_frame(d, '/raw_points')
            time.sleep(0.3)
            return p._bag_time_ns
        assert [step(+1) for _ in range(4)] == stamps
        assert [step(-1) for _ in range(3)] == stamps[2::-1]
        assert step(+1) == stamps[1]
    finally:
        p.shutdown()
        p.destroy_node()
        rclpy.shutdown()


@pytest.mark.skipif(not os.path.isdir(BAG), reason='sample bag not present')
def test_reverse_throttles_backward_clock():
    import rclpy
    from bag_player.player_core import BACKWARD_CLOCK_PERIOD_S, PlayerCore

    rclpy.init()
    p = PlayerCore(BAG)
    sent = []
    publish = p._clock_pub.publish
    p._clock_pub.publish = lambda m: (sent.append(m.clock.sec * 10**9 + m.clock.nanosec), publish(m))
    try:
        p.seek_seconds(30)
        time.sleep(0.2)
        p.set_rate_signed(-1.0)
        p.play()
        time.sleep(2.0)
        p.pause()
        time.sleep(0.5)
        backward = sum(b < a for a, b in zip(sent, sent[1:]))
        assert 2 <= backward <= 2.0 / BACKWARD_CLOCK_PERIOD_S + 3
        assert sent[-1] == p._bag_time_ns
    finally:
        p.shutdown()
        p.destroy_node()
        rclpy.shutdown()
