from glob import glob

from setuptools import find_packages, setup

package_name = "mrs_ethical_gate"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
        ("share/" + package_name + "/scenarios", glob("scenarios/*.yaml")),   # the fake planner's
    ],
    install_requires=["setuptools", "pydantic>=2", "pyyaml"],
    zip_safe=True,
    maintainer="MRS Ethics Team",
    maintainer_email="team@example.com",
    description="Module 5 gate: ProposedPlan in, EthicalVerdict (and ValidatedPlan on approval) out.",
    license="Apache-2.0",
    # Tells `colcon test` to run pytest (tests_require is ignored by setuptools >= 72).
    extras_require={"test": ["pytest"]},
    entry_points={
        "console_scripts": [
            "gate = mrs_ethical_gate.gate_node:main",
            # development node for testing the gate (dev_nodes/): a scripted stand-in for the planner
            "fake_planner = mrs_ethical_gate.dev_nodes.fake_planner:main",
        ],
    },
)
