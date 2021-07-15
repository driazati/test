from setuptools import setup


setup(
    name="aws_od_cli",
    version="1.0.0",
    description=("aws_od_cli"),
    author="me",
    entry_points={"console_scripts": ["aws_od_cli = aws_od_cli:cli"]},
    python_requires=">=3.7",
    install_requires=[
        "click==7.0",
        "boto3==1.16.52",
        "tabulate==0.8.9",
        "yaspin==2.0.0",
    ],
)