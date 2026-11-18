from glob import glob

from setuptools import find_packages, setup

package_name = "mrs_llm_planner"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    package_data={package_name: ["guidelines.yaml"]},
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
        ("share/" + package_name + "/config", glob("config/*.yaml") + glob("config/*.json")),
    ],
    install_requires=["setuptools", "pydantic>=2", "pyyaml"],
    zip_safe=False,
    maintainer="MRS Ethics Team",
    maintainer_email="team@example.com",
    description="Module 4: per-robot LLM agents and the dialogue coordinator.",
    license="Apache-2.0",
    extras_require={"test": ["pytest"]},
    entry_points={
        "console_scripts": [
            "agent = mrs_llm_planner.agent_node:main",
            "coordinator = mrs_llm_planner.coordinator_node:main",
            "summary = mrs_llm_planner.summary:main",
        ],
    },
)
