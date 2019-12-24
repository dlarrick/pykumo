import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="pykumo",
    version="0.1.1",
    author="Doug Larrick",
    author_email="doug@parkercat.org",
    description="Small library for interfacing with Mitsubishi KumoCloud enabled devices",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/dlarrick/pykumo",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
