"""Launch the bag player GUI together with RViz2 (using sim time).

Usage:
    ros2 launch bag_player player.launch.py bag:=/home/gs-omen/ros2bag/bag_records
    ros2 launch bag_player player.launch.py bag:=/path/to/bag rviz:=false
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


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

        Node(
            package='rviz2',
            executable='rviz2',
            name='rviz2',
            output='screen',
            condition=IfCondition(use_rviz),
            arguments=['-d', rviz_config],
            parameters=[{'use_sim_time': True}],
        ),
    ])
