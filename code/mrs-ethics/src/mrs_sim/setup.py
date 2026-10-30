from glob import glob

from setuptools import find_packages, setup

package_name = "mrs_sim"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    package_data={package_name: ["templates/*.sdf"]},
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
        # A world is a pair: <name>.sdf (Gazebo) + <name>.yaml (waypoint graph, zones, spawns).
        ("share/" + package_name + "/worlds", glob("worlds/*.sdf") + glob("worlds/*.yaml")),
    ],
    install_requires=["setuptools", "pydantic>=2", "pyyaml"],
    zip_safe=True,
    maintainer="MRS Ethics Team",
    maintainer_email="team@example.com",
    description="Gazebo worlds, multi-robot model, bridge and launch (Module 2).",
    license="Apache-2.0",
    # Tells `colcon test` to run pytest (tests_require is ignored by setuptools >= 72).
    extras_require={"test": ["pytest"]},
    entry_points={
        "console_scripts": [
            "sim_check = mrs_sim.sim_check:main",
            "list_worlds = mrs_sim.world_map:list_main",
        ],
    },
)
