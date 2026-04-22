"""Test targets: GoogleTest (C/C++) and Pytest (Python).

`devops test [<name>...]` selects everything whose class is a TestTarget
descendant (or all workspace test targets if no name given).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from devops.core.command import Command
from devops.core.target import Artifact, Target
from devops.targets.c_cpp import CCompile, ElfBinary, StaticLibrary, _resolve_sources

if TYPE_CHECKING:
    from devops.context import BuildContext
    from devops.targets.python import PythonWheel


class TestTarget(Artifact):
    """Marker base so `devops test` can select test targets."""

    def test_cmds(self, ctx: "BuildContext") -> list[Command]:
        raise NotImplementedError


class GoogleTest(CCompile, TestTarget):
    """Compile a GoogleTest binary that inherits its `target`'s compile env.

    Inherits from `target=`: flags, includes, defs, undefs, is_cxx=True.
    Links against the target if it's a StaticLibrary or ElfSharedObject,
    plus -lgtest -lgtest_main -lpthread by default.
    """

    def __init__(
        self,
        name: str,
        srcs: str | list[str],
        target: Target,
        extra_flags: tuple[str, ...] = (),
        extra_libs: tuple[str, ...] = ("gtest", "gtest_main", "pthread"),
        deps: dict[str, Target] | None = None,
        version: str | None = None,
    ):
        super().__init__(name=name, deps=deps, version=version)
        if not isinstance(target, CCompile):
            raise TypeError(
                f"GoogleTest target= must be a CCompile artifact, got {type(target).__name__}"
            )
        self._target_under_test = target
        self.srcs = _resolve_sources(self.project.root, srcs)
        self.includes = list(target.includes)
        self.flags = tuple(target.flags) + tuple(extra_flags)
        self.defs = dict(target.defs)
        self.undefs = tuple(target.undefs)
        self.is_cxx = True
        self._pic = False

        # Link against the thing being tested (if it's a library), plus gtest
        linkable: list[str | Target] = []
        if isinstance(target, (StaticLibrary,)) or type(target).__name__ == "ElfSharedObject":
            linkable.append(target)
        linkable.extend(extra_libs)
        self.libs = tuple(linkable)
        # Implicit dep so `devops test Foo` also builds the lib under test
        self.deps[f"_tested_{target.name}"] = target

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / self.name

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        out_dir = self.output_dir(ctx)
        compile_cmds, objs = self._compile_all(ctx, out_dir)
        lib_args, extra_inputs = self._link_flags_for_libs(ctx)
        tool = ctx.toolchain.cxx.resolved_for(
            workspace=ctx.workspace_root, project=self.project.root, cwd=self.project.root
        )
        link_argv = tool.invoke([*(str(o) for o in objs), *lib_args, "-o", str(self.output_path(ctx))])
        return [
            *compile_cmds,
            Command(
                argv=link_argv,
                cwd=self.project.root,
                label=f"link test {self.name}",
                inputs=(*objs, *extra_inputs),
                outputs=(self.output_path(ctx),),
            ),
        ]

    def test_cmds(self, ctx: "BuildContext") -> list[Command]:
        binpath = self.output_path(ctx)
        return [
            Command(
                argv=(str(binpath),),
                cwd=self.project.root,
                label=f"gtest {self.name}",
                inputs=(binpath,),
            )
        ]

    def lint_cmds(self, ctx: "BuildContext") -> list[Command]:
        from devops.tools import clang

        return clang.lint_for_ccompile(self, ctx)

    def describe(self) -> str:
        return (
            f"GoogleTest {self.qualified_name}\n"
            f"  tests:      {self._target_under_test.qualified_name}\n"
            f"  srcs:       {', '.join(s.name for s in self.srcs)}"
        )


class Pytest(TestTarget):
    """Run pytest against sources (usually a tests/ dir) of a PythonWheel."""

    def __init__(
        self,
        name: str,
        srcs: str | list[str],
        target: "PythonWheel | None" = None,
        deps: dict[str, Target] | None = None,
        version: str | None = None,
    ):
        super().__init__(name=name, deps=deps, version=version)
        self.srcs = _resolve_sources(self.project.root, srcs)
        self._target = target
        if target is not None:
            self.deps[f"_tested_{target.name}"] = target

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / ".pytest_stamp"

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        return []  # nothing to build

    def test_cmds(self, ctx: "BuildContext") -> list[Command]:
        pytest = ctx.toolchain.pytest.resolved_for(
            workspace=ctx.workspace_root, project=self.project.root, cwd=self.project.root
        )
        return [
            Command(
                argv=pytest.invoke([str(s) for s in self.srcs]),
                cwd=self.project.root,
                label=f"pytest {self.name}",
                inputs=tuple(self.srcs),
            )
        ]

    def describe(self) -> str:
        return (
            f"Pytest {self.qualified_name}\n"
            f"  target: {self._target.qualified_name if self._target else '-'}\n"
            f"  srcs:   {', '.join(s.name for s in self.srcs)}"
        )
