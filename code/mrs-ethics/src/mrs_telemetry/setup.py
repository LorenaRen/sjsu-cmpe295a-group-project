from glob import glob

from setuptools import find_packages, setup

package_name = "mrs_telemetry"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools", "pyyaml"],
    zip_safe=False,
    maintainer="MRS Ethics Team",
    maintainer_email="team@example.com",
    description="Module 7: one run folder per episode (MCAP bag, Gazebo state log, events.jsonl, run.json).",
    license="Apache-2.0",
    extras_require={"test": ["pytest"]},
    entry_points={
        "console_scripts": [
            "telemetry = mrs_telemetry.telemetry_node:main",
            "send_mission = mrs_telemetry.send_mission:main",
            "check_run = mrs_telemetry.check_run:main",
        ],
    },
)
