"""Public API for build.py files."""

from pathlib import Path

from devops import registry
from devops.options import COMMON_C_FLAGS, OptimizationLevel
from devops.remote import DirectoryRef, GitRef, Ref, TarballRef
from devops.targets.c_cpp import (
    CObjectFile,
    ElfBinary,
    ElfSharedObject,
    HeadersOnly,
    LdBinary,
    StaticLibrary,
    glob_sources,
)
from devops.targets.archive import CompressedArtifact, CompressionFormat
from devops.targets.copy import DirectoryArtifact, FileArtifact
from devops.targets.custom import CustomArtifact
from devops.targets.docs import SphinxDocs
from devops.targets.install import Install
from devops.targets.python import PythonApp, PythonShiv, PythonWheel
from devops.targets.script import Script
from devops.targets.tests import GoogleTest, Pytest
from devops.targets.zig import ZigBinary, ZigTest


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
    "CObjectFile",
    "LdBinary",
    "CustomArtifact",
    "FileArtifact",
    "DirectoryArtifact",
    "CompressedArtifact",
    "CompressionFormat",
    "PythonWheel",
    "PythonApp",
    "PythonShiv",
    "SphinxDocs",
    "Script",
    "GoogleTest",
    "Pytest",
    "Install",
    "ZigBinary",
    "ZigTest",
    "Ref",
    "GitRef",
    "TarballRef",
    "DirectoryRef",
    "glob",
]


# Plugin injection: any Target class a devops.targets entry point
# registered becomes importable as `from builder.plugins import FooBinary`.
# Keeping plugins in a separate namespace from core builtins makes the
# source of each Target obvious at every call site and eliminates the
# risk of a plugin shadowing a core class.
def _inject_plugin_classes() -> None:
    from builder import plugins as _plugins_ns
    from devops import plugins as _loader

    for plugin in _loader.load_plugins():
        for cls in plugin.classes:
            name = cls.__name__
            if hasattr(_plugins_ns, name) and getattr(_plugins_ns, name) is not cls:
                import sys

                print(
                    f"devops: plugin {plugin.name!r} tried to register "
                    f"{name!r} but that name is already bound — skipping",
                    file=sys.stderr,
                )
                continue
            setattr(_plugins_ns, name, cls)
            if name not in _plugins_ns.__all__:
                _plugins_ns.__all__.append(name)


_inject_plugin_classes()
