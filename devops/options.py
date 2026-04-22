"""Build profiles and shared flag presets."""

from __future__ import annotations

from enum import Enum


class OptimizationLevel(str, Enum):
    Debug = "Debug"
    Release = "Release"
    ReleaseSafe = "ReleaseSafe"

    @property
    def cflags(self) -> tuple[str, ...]:
        return {
            OptimizationLevel.Debug: ("-O0", "-ggdb", "-DDEBUG"),
            OptimizationLevel.Release: ("-O2", "-DNDEBUG"),
            OptimizationLevel.ReleaseSafe: ("-O2", "-ggdb", "-D_FORTIFY_SOURCE=2", "-fstack-protector-strong"),
        }[self]


COMMON_C_FLAGS: tuple[str, ...] = (
    "-Wall",
    "-Wextra",
    "-Wpedantic",
    "-fno-common",
    "-fstrict-aliasing",
)
