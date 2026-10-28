from setuptools import find_packages, setup

package_name = "mrs_common"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    package_data={package_name: ["samples/*.json"]},
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools", "pydantic>=2"],
    zip_safe=True,
    maintainer="MRS Ethics Team",
    maintainer_email="team@example.com",
    description="Shared plan schema, topic names, QoS and message helpers (Module 1).",
    license="Apache-2.0",
    # Tells `colcon test` to run pytest (tests_require is ignored by setuptools >= 72).
    extras_require={"test": ["pytest"]},
    entry_points={
        "console_scripts": [
            "check_interfaces = mrs_common.tools:check_main",
            "export_plan_schema = mrs_common.tools:export_schema_main",
            "sample_plan_pub = mrs_common.dev_nodes.sample_plan_pub:main",
            "plan_echo = mrs_common.dev_nodes.plan_echo:main",
        ],
    },
)
