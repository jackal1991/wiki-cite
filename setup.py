"""
Setup script for Wikipedia Citation & Cleanup Tool.
"""

from setuptools import find_packages, setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="wiki-cite",
    version="0.1.0",
    author="Wikipedia Citation Assistant Team",
    description="A tool for adding citations and cleanup to Wikipedia stub articles",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourorg/wiki-cite",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Topic :: Text Processing :: Markup",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.9",
    install_requires=[
        "anthropic>=0.34.0",
        "pydantic>=2.0.0",
        "pydantic-settings>=2.0.0",
        "mwclient>=0.10.1",
        "mwparserfromhell>=0.6.4",
        "flask>=3.0.0",
        "flask-cors>=4.0.0",
        "requests>=2.31.0",
        "beautifulsoup4>=4.12.0",
        "nltk>=3.8.1",
        "pyyaml>=6.0.1",
        "python-dotenv>=1.0.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-asyncio>=0.21.0",
            "pytest-mock>=3.11.0",
            "mypy>=1.5.0",
            "types-requests",
            "types-pyyaml",
        ],
    },
    entry_points={
        "console_scripts": [
            "wiki-cite=wiki_cite.cli:main",
        ],
    },
    include_package_data=True,
    package_data={
        "wiki_cite": ["templates/*.html"],
    },
)
