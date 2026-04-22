"""Public API for build.py files."""

from pathlib import Path

from devops import registry
from devops.options import COMMON_C_FLAGS, OptimizationLevel
from devops.targets.c_cpp import (
    ElfBinary,
    ElfSharedObject,
    HeadersOnly,
    StaticLibrary,
    glob_sources,
)
from devops.targets.docs import SphinxDocs
from devops.targets.python import PythonWheel
from devops.targets.script import Script
from devops.targets.tests import GoogleTest, Pytest


def glob(
    patterns: str | Path | list[str | Path],
    exclude: str | Path | list[str | Path] | None = None,
    allow_empty: bool = False,
) -> list[Path]:
    """Bazel-style glob: explicit expansion, returns a list of concrete paths.

    Use inside a build.py where a target's srcs=/includes= expects files:

        srcs = glob(["src/**/*.c", "main.c"], exclude=["src/**/*_test.c"])

    Globs are resolved relative to the current project's build.py directory.
    Raises if zero matches unless allow_empty=True.
    """
    proj = registry.current_project()
    return glob_sources(proj.root, patterns, exclude=exclude, allow_empty=allow_empty)


__all__ = [
    "COMMON_C_FLAGS",
    "OptimizationLevel",
    "ElfBinary",
    "ElfSharedObject",
    "StaticLibrary",
    "HeadersOnly",
    "PythonWheel",
    "SphinxDocs",
    "Script",
    "GoogleTest",
    "Pytest",
    "glob",
]
