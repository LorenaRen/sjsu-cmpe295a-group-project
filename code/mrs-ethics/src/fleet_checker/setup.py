from setuptools import find_packages, setup

package_name = "fleet_checker"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools", "pydantic>=2", "pyyaml"],
    zip_safe=True,
    maintainer="MRS Ethics Team",
    maintainer_email="team@example.com",
    description="Shared checker (core of Module 5): plan format, cross-plan, policy and fairness rules.",
    license="Apache-2.0",
    # Tells `colcon test` to run pytest (tests_require is ignored by setuptools >= 72).
    extras_require={"test": ["pytest"]},
)
