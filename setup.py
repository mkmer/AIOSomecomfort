from setuptools import setup

install_requires = list(val.strip() for val in open("requirements.txt"))
tests_require = list(val.strip() for val in open("test_requirements.txt"))

with open("README.rst", "r") as fh:
    long_description = fh.read()

setup(
    name="AIOSomecomfort",
    version="0.0.18",
    description="A client for Honeywell's US-based cloud devices",
    license="MIT",
    long_description=long_description,
    long_description_content_type="text/plain",
    author="Mike Kasper",
    author_email="m_kasper@sbcglobal.net",
    url="https://github.com/mkmer/AIOSomecomfort",
    download_url="https://github.com/mkmer/AIOSomecomfort/archive/refs/tags/0.0.18.tar.gz",
    packages=["aiosomecomfort"],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
    ],
    install_requires=install_requires,
    tests_require=tests_require,
    include_package_data=True,
)
