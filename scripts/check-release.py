"""Reject a release tag that does not match the single package version source."""

import os
import runpy
from pathlib import Path


def validate_tag(tag: str, version: str) -> None:
    if tag != f"v{version}":
        raise ValueError(f"Release tag must be v{version}; got {tag!r}")


if __name__ == "__main__":
    version = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "src/januaryai/_version.py")
    )["__version__"]
    validate_tag(os.environ.get("GITHUB_REF_NAME", ""), version)
    print(f"Release tag matches package version {version}")
