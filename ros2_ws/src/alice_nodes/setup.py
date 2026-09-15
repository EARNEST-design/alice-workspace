from setuptools import find_packages, setup


package_name = "alice_nodes"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
    ],
    install_requires=["setuptools"],
    entry_points={"console_scripts": [f"{role} = alice_nodes.{role}:main" for role in ("session", "tts", "audio", "expression", "motion", "maestro", "perception", "recorder")] + ["alice = alice_nodes.cli:main"]},
    zip_safe=True,
    maintainer="Alice maintainers",
    maintainer_email="alice@example.invalid",
    description="Pure transport guards and ROS adapters for Alice.",
    license="Proprietary",
)
