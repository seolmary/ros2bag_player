"""Launch the bag player GUI together with RViz2 (using sim time).

Usage:
    ros2 launch bag_player player.launch.py bag:=/home/gs-omen/ros2bag/bag_records
    ros2 launch bag_player player.launch.py bag:=/path/to/bag rviz:=false
"""

import os

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare

DISPLAY_FOR_TYPE = {
    'sensor_msgs/msg/PointCloud2': 'rviz_default_plugins/PointCloud2',
    'sensor_msgs/msg/LaserScan': 'rviz_default_plugins/LaserScan',
    'sensor_msgs/msg/Image': 'rviz_default_plugins/Image',
    'sensor_msgs/msg/CompressedImage': 'rviz_default_plugins/Image',
    'grid_map_msgs/msg/GridMap': 'grid_map_rviz_plugin/GridMap',
    'visualization_msgs/msg/Marker': 'rviz_default_plugins/Marker',
    'visualization_msgs/msg/MarkerArray': 'rviz_default_plugins/MarkerArray',
    'nav_msgs/msg/Odometry': 'rviz_default_plugins/Odometry',
    'nav_msgs/msg/Path': 'rviz_default_plugins/Path',
    'nav_msgs/msg/OccupancyGrid': 'rviz_default_plugins/Map',
    'geometry_msgs/msg/PoseStamped': 'rviz_default_plugins/Pose',
    'geometry_msgs/msg/PoseArray': 'rviz_default_plugins/PoseArray',
}


def rviz_config_for_bag(bag_dir, base_config, out_path):
    """Write base_config to out_path with its displays matched to the bag's topics."""
    with open(os.path.join(bag_dir, 'metadata.yaml')) as f:
        meta = yaml.safe_load(f)['rosbag2_bagfile_information']
    wanted = {}
    for entry in meta['topics_with_message_count']:
        t = entry['topic_metadata']
        if t['type'] in DISPLAY_FOR_TYPE:
            wanted[t['name']] = DISPLAY_FOR_TYPE[t['type']]

    with open(base_config) as f:
        cfg = yaml.safe_load(f)
    vm = cfg['Visualization Manager']

    def topic_of(d):
        return (d.get('Topic') or {}).get('Value')

    displays = [d for d in vm['Displays']
                if topic_of(d) is None or wanted.get(topic_of(d)) == d.get('Class')]
    have = {topic_of(d) for d in displays}
    for topic in sorted(wanted):
        if topic not in have:
            displays.append({'Class': wanted[topic], 'Name': topic, 'Enabled': True,
                             'Topic': {'Value': topic}, 'Value': True})
    vm['Displays'] = displays

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    return out_path


def generate_launch_description():
    pkg_share = get_package_share_directory('bag_player')
    default_rviz = os.path.join(pkg_share, 'rviz', 'bag_player.rviz')
    default_urdf = os.path.join(pkg_share, 'urdf', 'rbq10.urdf')

    bag = LaunchConfiguration('bag')
    storage = LaunchConfiguration('storage')
    use_rviz = LaunchConfiguration('rviz')
    rviz_config = LaunchConfiguration('rviz_config')
    use_robot = LaunchConfiguration('robot_model')
    urdf = LaunchConfiguration('urdf')

    # robot_state_publisher needs the URDF as a string parameter. Read it at
    # launch time so a plain .urdf file works without xacro.
    def _load_robot_description(context):
        urdf_path = urdf.perform(context)
        with open(urdf_path, 'r') as f:
            robot_description = f.read()
        return [
            Node(
                package='robot_state_publisher',
                executable='robot_state_publisher',
                name='robot_state_publisher',
                output='screen',
                condition=IfCondition(use_robot),
                parameters=[{
                    'robot_description': robot_description,
                    'use_sim_time': True,
                }],
            ),
            # The bag's /tf uses `base_link` as the robot base, but the URDF is
            # rooted at `base`. Bridge them with an identity transform so the
            # robot connects to map->odom->base_link.
            Node(
                package='tf2_ros',
                executable='static_transform_publisher',
                name='base_link_to_base',
                output='screen',
                condition=IfCondition(use_robot),
                arguments=['--frame-id', 'base_link', '--child-frame-id', 'base'],
                parameters=[{'use_sim_time': True}],
            ),
        ]

    def _launch_rviz(context):
        bag_dir = bag.perform(context)
        base = rviz_config.perform(context)
        out = os.path.join(os.path.expanduser('~/.cache/bag_player/rviz'),
                           os.path.basename(os.path.normpath(bag_dir)) + '.rviz')
        if base == default_rviz and os.path.exists(out):
            base = out
        try:
            config = rviz_config_for_bag(bag_dir, base, out)
            print(f'[player.launch] rviz config: {config} (base: {base})')
        except Exception as exc:
            print(f'[player.launch] could not generate rviz config ({exc}); using {base}')
            config = base
        return [
            Node(
                package='rviz2',
                executable='rviz2',
                name='rviz2',
                output='screen',
                condition=IfCondition(use_rviz),
                arguments=['-d', config],
                parameters=[{'use_sim_time': True}],
            ),
        ]

    return LaunchDescription([
        DeclareLaunchArgument('bag', description='Path to the bag directory.'),
        DeclareLaunchArgument('storage', default_value='mcap',
                              description='Storage id (mcap or sqlite3).'),
        DeclareLaunchArgument('rviz', default_value='true',
                              description='Also launch RViz2.'),
        DeclareLaunchArgument('rviz_config', default_value=default_rviz,
                              description='RViz2 config file.'),
        DeclareLaunchArgument('robot_model', default_value='true',
                              description='Publish the RBQ10 URDF via robot_state_publisher.'),
        DeclareLaunchArgument('urdf', default_value=default_urdf,
                              description='URDF file for the robot model.'),

        Node(
            package='bag_player',
            executable='bag_player',
            name='bag_player',
            output='screen',
            arguments=['--bag', bag, '--storage', storage],
        ),

        OpaqueFunction(function=_load_robot_description),

        OpaqueFunction(function=_launch_rviz),
    ])
