from setuptools import setup


setup(
    name="aws_od_cli",
    version="1.0.0",
    description=("aws_od_cli"),
    author="me",
    entry_points={"console_scripts": ["aws_od_cli = aws_od_cli:cli"]},
    python_requires=">=3.7",
    install_requires=[
        "click"
    ],
)