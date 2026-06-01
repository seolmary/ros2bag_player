"""Launch the bag player GUI together with RViz2 (using sim time).

Usage:
    ros2 launch bag_player player.launch.py bag:=/home/gs-omen/ros2bag/bag_records
    ros2 launch bag_player player.launch.py bag:=/path/to/bag rviz:=false
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    pkg_share = get_package_share_directory('bag_player')
    default_rviz = os.path.join(pkg_share, 'rviz', 'bag_player.rviz')

    bag = LaunchConfiguration('bag')
    storage = LaunchConfiguration('storage')
    use_rviz = LaunchConfiguration('rviz')
    rviz_config = LaunchConfiguration('rviz_config')

    return LaunchDescription([
        DeclareLaunchArgument('bag', description='Path to the bag directory.'),
        DeclareLaunchArgument('storage', default_value='mcap',
                              description='Storage id (mcap or sqlite3).'),
        DeclareLaunchArgument('rviz', default_value='true',
                              description='Also launch RViz2.'),
        DeclareLaunchArgument('rviz_config', default_value=default_rviz,
                              description='RViz2 config file.'),

        Node(
            package='bag_player',
            executable='bag_player',
            name='bag_player',
            output='screen',
            arguments=['--bag', bag, '--storage', storage],
        ),

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
