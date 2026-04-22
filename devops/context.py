"""BuildContext — everything a target needs to compute its commands.

Tools are argv-prefixes, not single executable paths. This lets a toolchain
entry wrap the real compiler in docker, podman, a remote runner, etc.:

    [toolchain]
    cc = ["docker", "run", "--rm",
          "-v", "{workspace}:{workspace}", "-w", "{cwd}",
          "ghcr.io/team/toolchain:v3", "clang"]
    clang_tidy = "clang-tidy"          # string is shorthand for [<str>]

Placeholders expanded per-command:
    {workspace}  — absolute workspace root
    {project}    — absolute project root (dir containing build.py)
    {cwd}        — Command.cwd (falls back to project root)

Mount the workspace at the same path inside the container
(``-v {workspace}:{workspace}``) so host and container paths coincide — the
framework assumes this and doesn't otherwise translate paths.
"""

from __future__ import annotations

import shutil
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from devops.options import OptimizationLevel


@dataclass(frozen=True)
class Tool:
    """An argv prefix. invoke(args) returns the full argv for a Command."""

    argv: tuple[str, ...]

    @classmethod
    def of(cls, spec: str | list[str] | tuple[str, ...] | "Tool") -> "Tool":
        if isinstance(spec, Tool):
            return spec
        if isinstance(spec, str):
            return cls(argv=(spec,))
        return cls(argv=tuple(spec))

    def invoke(self, args: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        return (*self.argv, *args)

    def resolved_for(self, *, workspace: Path, project: Path, cwd: Path | None) -> "Tool":
        subs = {
            "workspace": str(workspace),
            "project": str(project),
            "cwd": str(cwd or project),
        }
        return Tool(argv=tuple(a.format(**subs) for a in self.argv))

    def is_available(self) -> bool:
        """True if the first arg (the real executable) is on PATH."""
        return shutil.which(self.argv[0]) is not None or Path(self.argv[0]).is_file()


_DEFAULT_TOOLS = {
    "cc": "clang",
    "cxx": "clang++",
    "ar": "ar",
    "clang_tidy": "clang-tidy",
    "clang_format": "clang-format",
    "cppcheck": "cppcheck",
    "black": "black",
    "ruff": "ruff",
    "sphinx_build": "sphinx-build",
    "pytest": "pytest",
    "python": "python3",
}


@dataclass
class Toolchain:
    cc: Tool = field(default_factory=lambda: Tool.of("clang"))
    cxx: Tool = field(default_factory=lambda: Tool.of("clang++"))
    ar: Tool = field(default_factory=lambda: Tool.of("ar"))
    clang_tidy: Tool = field(default_factory=lambda: Tool.of("clang-tidy"))
    clang_format: Tool = field(default_factory=lambda: Tool.of("clang-format"))
    cppcheck: Tool = field(default_factory=lambda: Tool.of("cppcheck"))
    black: Tool = field(default_factory=lambda: Tool.of("black"))
    ruff: Tool = field(default_factory=lambda: Tool.of("ruff"))
    sphinx_build: Tool = field(default_factory=lambda: Tool.of("sphinx-build"))
    pytest: Tool = field(default_factory=lambda: Tool.of("pytest"))
    python: Tool = field(default_factory=lambda: Tool.of("python3"))

    @classmethod
    def from_config(cls, cfg: dict | None) -> "Toolchain":
        tc = cls()
        if not cfg:
            return tc
        known = {f.name for f in fields(cls)}
        for key, spec in cfg.items():
            if key not in known:
                raise ValueError(f"unknown toolchain entry {key!r}; known: {sorted(known)}")
            setattr(tc, key, Tool.of(spec))
        return tc


def load_toolchain(workspace_root: Path) -> Toolchain:
    cfg_path = workspace_root / "devops.toml"
    if not cfg_path.is_file():
        return Toolchain()
    with cfg_path.open("rb") as f:
        data = tomllib.load(f)
    return Toolchain.from_config(data.get("toolchain"))


@dataclass
class BuildContext:
    workspace_root: Path
    build_dir: Path
    profile: OptimizationLevel = OptimizationLevel.Debug
    jobs: int = 1
    verbose: bool = False
    dry_run: bool = False
    toolchain: Toolchain = field(default_factory=Toolchain)

    def project_out(self, project_name: str, target_name: str) -> Path:
        return self.build_dir / self.profile.value / project_name / target_name
