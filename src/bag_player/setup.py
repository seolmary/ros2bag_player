from glob import glob
import os

from setuptools import find_packages, setup

package_name = 'bag_player'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.launch.py')),
        (os.path.join('share', package_name, 'rviz'), glob('rviz/*.rviz')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='gs-omen',
    maintainer_email='rktjd9015@gmail.com',
    description='Video-player style controller for ROS 2 bags.',
    license='MIT',
    entry_points={
        'console_scripts': [
            'bag_player = bag_player.app:main',
        ],
    },
)
