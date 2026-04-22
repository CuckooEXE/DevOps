"""Command record — a recipe for something to execute."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    cwd: Path | None = None
    env: tuple[tuple[str, str], ...] = ()
    shell: bool = False
    label: str = ""  # short human-readable description ("compile foo.c", "link MyCoolApp")
    # inputs/outputs drive the incremental cache stamp
    inputs: tuple[Path, ...] = field(default_factory=tuple)
    outputs: tuple[Path, ...] = field(default_factory=tuple)
    # Optional Makefile-style depfile emitted by the command itself (e.g.
    # `clang -MMD -MF <path>`). When present, the cache parses it after each
    # run so the *next* cache check stats every header the compiler actually
    # saw — not just the source file listed in `inputs`.
    depfile: Path | None = None

    @classmethod
    def argv_cmd(cls, argv: list[str] | tuple[str, ...], **kwargs: object) -> Command:
        return cls(argv=tuple(argv), **kwargs)  # type: ignore[arg-type]

    @classmethod
    def shell_cmd(cls, line: str, **kwargs: object) -> Command:
        return cls(argv=(line,), shell=True, **kwargs)  # type: ignore[arg-type]

    def rendered(self) -> str:
        if self.shell:
            return self.argv[0]
        return shlex.join(self.argv)
