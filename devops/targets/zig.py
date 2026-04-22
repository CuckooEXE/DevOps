"""ZigBinary — wraps `zig build` against a project's build.zig.

Zig has its own build system. The idiomatic `zig init` layout is:

    myproj/
    ├── build.zig
    ├── build.zig.zon
    └── src/
        └── main.zig

`zig build` processes build.zig and drops outputs under `zig-out/`. We let
zig drive the compile (it knows its own language best) but redirect the
install prefix into the devops build tree so:

  - incremental / clean / describe work like any other target
  - the produced binary's path is predictable for downstream targets

Usage:

    ZigBinary(
        name="ziggy",
        project_dir="zigproj",   # dir containing build.zig, rel to build.py
        exe="ziggy",             # filename zig produces under bin/ (default=name)
        zig_args=("-Dextra=1",), # extra args to `zig build`
    )
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from devops.core.command import Command
from devops.core.target import Artifact, Target
from devops.options import OptimizationLevel
from devops.targets.tests import TestTarget

if TYPE_CHECKING:
    from devops.context import BuildContext


_PROFILE_TO_ZIG_MODE = {
    OptimizationLevel.Debug: "Debug",
    OptimizationLevel.Release: "ReleaseFast",
    OptimizationLevel.ReleaseSafe: "ReleaseSafe",
}


class ZigBinary(Artifact):
    """An executable built by `zig build` from a project's build.zig."""

    def __init__(
        self,
        name: str,
        project_dir: str | Path = ".",
        exe: str | None = None,
        zig_args: tuple[str, ...] = (),
        version: str | None = None,
        deps: dict[str, Target] | None = None,
        doc: str | None = None,
        arch: str = "host",
        extra_inputs: "tuple[str | Path, ...] | list[str | Path] | None" = None,
    ) -> None:
        super().__init__(
            name=name, deps=deps, version=version, doc=doc, arch=arch,
            extra_inputs=extra_inputs,
        )
        self._project_dir: Path = (self.project.root / project_dir).resolve()
        self._build_zig: Path = self._project_dir / "build.zig"
        self.exe_name: str = exe or name
        self.zig_args: tuple[str, ...] = tuple(zig_args)

        if not self._project_dir.is_dir():
            raise FileNotFoundError(f"ZigBinary({name!r}): project_dir {self._project_dir} not found")
        if not self._build_zig.is_file():
            raise FileNotFoundError(
                f"ZigBinary({name!r}): expected build.zig at {self._build_zig}"
            )

    def output_path(self, ctx: "BuildContext") -> Path:
        # zig build --prefix <out> lays things out as <out>/bin/<exe>
        return self.output_dir(ctx) / "bin" / self.exe_name

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        zig = ctx.toolchain_for(self.arch).zig.resolved_for(
            workspace=ctx.workspace_root,
            project=self._project_dir,
            cwd=self._project_dir,
        )
        mode = _PROFILE_TO_ZIG_MODE[ctx.profile]
        argv = zig.invoke([
            "build",
            f"-Doptimize={mode}",
            "--prefix", str(self.output_dir(ctx)),
            *self.zig_args,
        ])
        return [
            Command(
                argv=argv,
                cwd=self._project_dir,
                label=f"zig build {self.name}",
                inputs=(self._build_zig, *self._extra_inputs),
                outputs=(self.output_path(ctx),),
            )
        ]

    def lint_cmds(self, ctx: "BuildContext") -> list[Command]:
        """`zig fmt --check <project_dir>` — the idiomatic zig lint."""
        zig = ctx.toolchain_for(self.arch).zig.resolved_for(
            workspace=ctx.workspace_root,
            project=self._project_dir,
            cwd=self._project_dir,
        )
        return [
            Command(
                argv=zig.invoke(["fmt", "--check", str(self._project_dir)]),
                cwd=self._project_dir,
                label=f"zig fmt --check {self.name}",
            )
        ]

    def describe(self) -> str:
        return (
            f"ZigBinary {self.qualified_name}\n"
            f"  project: {self._project_dir}\n"
            f"  exe:     bin/{self.exe_name}"
        )


class ZigTest(TestTarget):
    """Delegates to ``zig build test`` for the project's build.zig.

    ``zig build test`` compiles the test artifacts declared in build.zig
    and runs them in one shot, so this target has an empty ``build_cmds``
    and does all its work in ``test_cmds``.

    Like GoogleTest/Pytest, this is pinned to ``arch="host"`` — tests
    run on your laptop even when the production target is a cross
    compile.

    Args:
        name:        target name
        project_dir: directory containing build.zig (default ".")
        zig_args:    extra args appended to ``zig build test``
    """

    def __init__(
        self,
        name: str,
        project_dir: str | Path = ".",
        zig_args: tuple[str, ...] = (),
        version: str | None = None,
        deps: dict[str, Target] | None = None,
        doc: str | None = None,
    ) -> None:
        super().__init__(name=name, deps=deps, version=version, doc=doc, arch="host")
        self._project_dir: Path = (self.project.root / project_dir).resolve()
        self._build_zig: Path = self._project_dir / "build.zig"
        self.zig_args = tuple(zig_args)

        if not self._project_dir.is_dir():
            raise FileNotFoundError(f"ZigTest({name!r}): project_dir {self._project_dir} not found")
        if not self._build_zig.is_file():
            raise FileNotFoundError(f"ZigTest({name!r}): expected build.zig at {self._build_zig}")

    def output_path(self, ctx: "BuildContext") -> Path:
        # Sentinel so the artifact contract is satisfied; zig build test
        # doesn't produce a single canonical binary we can point at.
        return self.output_dir(ctx) / ".zig-test-stamp"

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        return []

    def test_cmds(self, ctx: "BuildContext") -> list[Command]:
        zig = ctx.toolchain_for(self.arch).zig.resolved_for(
            workspace=ctx.workspace_root,
            project=self._project_dir,
            cwd=self._project_dir,
        )
        mode = _PROFILE_TO_ZIG_MODE[ctx.profile]
        argv = zig.invoke([
            "build", "test",
            f"-Doptimize={mode}",
            *self.zig_args,
        ])
        return [
            Command(
                argv=argv,
                cwd=self._project_dir,
                label=f"zig build test {self.name}",
                inputs=(self._build_zig,),
            )
        ]

    def describe(self) -> str:
        return (
            f"ZigTest {self.qualified_name}\n"
            f"  project: {self._project_dir}"
        )
