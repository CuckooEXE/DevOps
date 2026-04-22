"""BuildContext — everything a target needs to compute its commands.

Tools are argv-prefixes, not single executable paths. This lets a toolchain
entry wrap the real compiler in docker, podman, a remote runner, etc.:

    [toolchain]                         # "host" toolchain (default)
    cc = ["docker", "run", "--rm",
          "-v", "{workspace}:{workspace}", "-w", "{cwd}",
          "ghcr.io/team/toolchain:v3", "clang"]
    clang_tidy = "clang-tidy"          # string is shorthand for [<str>]

    [toolchain.aarch64]                 # cross-compile toolchain
    cc  = ["aarch64-linux-gnu-gcc"]
    cxx = ["aarch64-linux-gnu-g++"]
    ar  = "aarch64-linux-gnu-ar"

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
    ld: Tool = field(default_factory=lambda: Tool.of("ld"))
    clang_tidy: Tool = field(default_factory=lambda: Tool.of("clang-tidy"))
    clang_format: Tool = field(default_factory=lambda: Tool.of("clang-format"))
    cppcheck: Tool = field(default_factory=lambda: Tool.of("cppcheck"))
    black: Tool = field(default_factory=lambda: Tool.of("black"))
    ruff: Tool = field(default_factory=lambda: Tool.of("ruff"))
    sphinx_build: Tool = field(default_factory=lambda: Tool.of("sphinx-build"))
    pytest: Tool = field(default_factory=lambda: Tool.of("pytest"))
    python: Tool = field(default_factory=lambda: Tool.of("python3"))
    zig: Tool = field(default_factory=lambda: Tool.of("zig"))
    shiv: Tool = field(default_factory=lambda: Tool.of("shiv"))

    @classmethod
    def from_config(cls, cfg: dict[str, object] | None) -> "Toolchain":
        tc = cls()
        if not cfg:
            return tc
        known = {f.name for f in fields(cls)}
        for key, spec in cfg.items():
            if key not in known:
                raise ValueError(f"unknown toolchain entry {key!r}; known: {sorted(known)}")
            if not isinstance(spec, (str, list, tuple, Tool)):
                raise TypeError(f"toolchain[{key}] must be str/list/tuple/Tool, got {type(spec).__name__}")
            setattr(tc, key, Tool.of(spec))
        return tc


HOST_ARCH = "host"


def load_toolchains(workspace_root: Path) -> dict[str, Toolchain]:
    """Load per-arch toolchains from devops.toml.

    The top-level ``[toolchain]`` table is the host toolchain. Nested
    ``[toolchain.<arch>]`` tables are per-arch toolchains. A key is both
    only if it's a string/list/tuple; a sub-table is treated as a nested
    arch definition.
    """
    result: dict[str, Toolchain] = {HOST_ARCH: Toolchain()}
    cfg_path = workspace_root / "devops.toml"
    if not cfg_path.is_file():
        return result
    with cfg_path.open("rb") as f:
        data = tomllib.load(f)
    tc_section = data.get("toolchain")
    if not tc_section:
        return result
    host_entries: dict[str, object] = {}
    for key, value in tc_section.items():
        if isinstance(value, dict):
            # sub-table → a per-arch toolchain
            result[key] = Toolchain.from_config(value)
        else:
            host_entries[key] = value
    if host_entries:
        result[HOST_ARCH] = Toolchain.from_config(host_entries)
    return result


# Back-compat shim — still used by CLI `_prepare()` for the primary toolchain.
def load_toolchain(workspace_root: Path) -> Toolchain:
    return load_toolchains(workspace_root)[HOST_ARCH]


@dataclass
class BuildContext:
    workspace_root: Path
    build_dir: Path
    profile: OptimizationLevel = OptimizationLevel.Debug
    jobs: int = 1
    verbose: bool = False
    dry_run: bool = False
    # Primary toolchain — kept for back-compat; equivalent to toolchains[HOST_ARCH].
    toolchain: Toolchain = field(default_factory=Toolchain)
    toolchains: dict[str, Toolchain] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Ensure the primary toolchain is addressable via toolchains[HOST_ARCH]
        if HOST_ARCH not in self.toolchains:
            self.toolchains[HOST_ARCH] = self.toolchain

    def toolchain_for(self, arch: str) -> Toolchain:
        if arch not in self.toolchains:
            available = sorted(self.toolchains.keys())
            raise ValueError(
                f"no toolchain configured for arch {arch!r}; available: {available}. "
                f"Declare [toolchain.{arch}] in devops.toml."
            )
        return self.toolchains[arch]

    def project_out(self, project_name: str, target_name: str, arch: str = HOST_ARCH) -> Path:
        # Separate build trees per arch so host + cross builds coexist.
        return self.build_dir / self.profile.value / arch / project_name / target_name
