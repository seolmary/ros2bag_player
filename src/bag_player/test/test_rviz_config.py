"""Smallest checks that fail if the per-bag RViz config or the PNG relabel breaks."""
import importlib.util
import os
import struct
import zlib

import yaml

HERE = os.path.dirname(__file__)
PKG = os.path.dirname(HERE)


def _launch_module():
    spec = importlib.util.spec_from_file_location(
        'player_launch', os.path.join(PKG, 'launch', 'player.launch.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rviz_config_for_bag(tmp_path):
    bag = tmp_path / 'bag'
    bag.mkdir()
    topics = [('/raw_points', 'sensor_msgs/msg/PointCloud2'),
              ('/cam/depth/compressed', 'sensor_msgs/msg/CompressedImage'),
              ('/tf', 'tf2_msgs/msg/TFMessage')]
    (bag / 'metadata.yaml').write_text(yaml.safe_dump({'rosbag2_bagfile_information': {
        'topics_with_message_count': [{'topic_metadata': {'name': n, 'type': t}}
                                      for n, t in topics]}}))
    out = _launch_module().rviz_config_for_bag(
        str(bag), os.path.join(PKG, 'rviz', 'bag_player.rviz'), str(tmp_path / 'out.rviz'))
    displays = yaml.safe_load(open(out))['Visualization Manager']['Displays']
    by_topic = {(d.get('Topic') or {}).get('Value'): d for d in displays}
    assert 'RobotModel' in {d['Name'] for d in displays}        # topic-less displays kept
    assert by_topic['/raw_points']['Name'] == 'PointCloud2'     # saved display reused
    assert by_topic['/cam/depth/compressed']['Class'] == 'rviz_default_plugins/Image'
    assert '/ouster/points' not in by_topic                     # not in the bag -> dropped
    assert None in by_topic and len(by_topic) == 3              # nothing for /tf


def test_fix_compressed_format():
    from bag_player.player_core import _fix_compressed_format
    from sensor_msgs.msg import CompressedImage

    def png(bit_depth, colour_type):
        ihdr = struct.pack('>IIBBBBB', 4, 4, bit_depth, colour_type, 0, 0, 0)
        return (b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + ihdr
                + struct.pack('>I', zlib.crc32(b'IHDR' + ihdr)))

    msg = CompressedImage(format='png', data=png(16, 0))
    _fix_compressed_format(msg)
    assert msg.format == '16UC1; png'
    for fmt, data in [('png', png(8, 0)), ('jpeg', b'\xff\xd8' + b'\0' * 30), ('16UC1; png', png(16, 0))]:
        msg = CompressedImage(format=fmt, data=data)
        _fix_compressed_format(msg)
        assert msg.format == fmt
