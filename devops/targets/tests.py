"""Test targets: GoogleTest (C/C++), Pytest (Python), and TestRangeTest
(libvirt-backed e2e via the ``testrange`` pip package).

`devops test [<name>...]` selects everything whose class is a TestTarget
descendant (or all workspace test targets if no name given).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from devops.core.command import Command
from devops.core.target import Artifact, Target
from devops.targets.c_cpp import CCompile, ElfBinary, SourcesSpec, StaticLibrary, _resolve_sources

if TYPE_CHECKING:
    from devops.context import BuildContext
    from devops.targets.python import PythonWheel


class TestTarget(Artifact):
    """Marker base so `devops test` can select test targets."""

    # Suppress pytest's auto-collection of any subclass whose name starts
    # with "Test" (e.g. TestRangeTest) — these are devops build targets,
    # not pytest test classes.
    __test__ = False

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
        srcs: SourcesSpec,
        target: Target,
        extra_flags: tuple[str, ...] = (),
        extra_libs: tuple[str, ...] = ("gtest", "gtest_main", "pthread"),
        deps: dict[str, Target] | None = None,
        version: str | None = None,
        doc: str | None = None,
    ) -> None:
        # Tests always build for host arch — you want `devops test` to run
        # locally even when the thing under test is cross-compiled.
        super().__init__(name=name, deps=deps, version=version, doc=doc, arch="host")
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

        # Linkable inputs:
        #   - if target is a library, link against it directly
        #   - otherwise (ElfBinary under test), link against everything the
        #     binary itself links, so tests see the same library env
        #   - plus gtest / gtest_main / pthread (extra_libs)
        linkable: list[str | Target] = []
        if isinstance(target, StaticLibrary) or type(target).__name__ == "ElfSharedObject":
            linkable.append(target)
        elif isinstance(target, ElfBinary):
            linkable.extend(target.libs)
        linkable.extend(extra_libs)
        self.libs = tuple(linkable)
        # Implicit dep so `devops test Foo` also builds the thing being tested
        self.deps[f"_tested_{target.name}"] = target

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / self.name

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        out_dir = self.output_dir(ctx)
        compile_cmds, objs = self._compile_all(ctx, out_dir)
        lib_args, extra_inputs = self._link_flags_for_libs(ctx)
        tool = ctx.toolchain_for(self.arch).cxx.resolved_for(
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
        srcs: SourcesSpec,
        target: "PythonWheel | None" = None,
        deps: dict[str, Target] | None = None,
        version: str | None = None,
        doc: str | None = None,
    ) -> None:
        super().__init__(name=name, deps=deps, version=version, doc=doc)
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
        # When testing against a PythonWheel, prepend its src dir to PYTHONPATH
        # so `from <pkg> import ...` works without installing the wheel first.
        env: tuple[tuple[str, str], ...] = ()
        if self._target is not None:
            pkg_dir = str(self._target.pyproject.parent)
            env = (("PYTHONPATH", pkg_dir),)
        return [
            Command(
                argv=pytest.invoke([str(s) for s in self.srcs]),
                cwd=self.project.root,
                env=env,
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


class TestRangeTest(TestTarget):
    """Run libvirt-backed e2e tests via the ``testrange`` pip package.

    Each ``srcs`` entry is a Python file exposing a ``gen_tests`` factory
    (override with ``factory=``). At ``devops test`` time we invoke
    ``testrange run <src>:<factory>`` once per src.

    ``artifacts`` maps stable aliases to built Targets. Each alias
    materializes as a ``DEVOPS_ARTIFACT_<ALIAS>`` env var on the
    ``testrange`` invocation, so the test function can discover
    built-binary paths without hardcoding them:

        artifacts={"app": myBinary}   →   os.environ["DEVOPS_ARTIFACT_APP"]

    Every artifact flows into ``self.deps`` so topo-sort builds them
    before the test runs.

    The ``testrange`` binary itself is looked up via
    ``ctx.toolchain.testrange`` — install it globally (pip install
    testrange) or override the invocation in ``devops.toml``.
    """

    def __init__(
        self,
        name: str,
        srcs: SourcesSpec,
        artifacts: dict[str, Target] | None = None,
        factory: str = "gen_tests",
        env: dict[str, str] | None = None,
        deps: dict[str, Target] | None = None,
        version: str | None = None,
        doc: str | None = None,
    ) -> None:
        super().__init__(name=name, deps=deps, version=version, doc=doc)
        self.srcs = _resolve_sources(self.project.root, srcs)
        self._artifacts: dict[str, Target] = dict(artifacts or {})
        self.factory = factory
        self._env = dict(env or {})
        for alias, artifact in self._artifacts.items():
            self.deps[f"_artifact_{alias}"] = artifact

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / ".testrange_stamp"

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        return []  # testrange is global; referenced artifacts build via deps

    def test_cmds(self, ctx: "BuildContext") -> list[Command]:
        testrange = ctx.toolchain.testrange.resolved_for(
            workspace=ctx.workspace_root,
            project=self.project.root,
            cwd=self.project.root,
        )
        env: list[tuple[str, str]] = []
        artifact_inputs: list[Path] = []
        for alias, artifact in self._artifacts.items():
            out = artifact.output_path(ctx)
            env.append((f"DEVOPS_ARTIFACT_{alias.upper()}", str(out)))
            artifact_inputs.append(out)
        for k, v in self._env.items():
            env.append((k, v))
        env_tuple = tuple(env)
        return [
            Command(
                argv=testrange.invoke(["run", f"{src}:{self.factory}"]),
                cwd=self.project.root,
                env=env_tuple,
                label=f"testrange {self.name} / {src.name}",
                inputs=(src, *artifact_inputs),
            )
            for src in self.srcs
        ]

    def describe(self) -> str:
        if self._artifacts:
            alias_list = ", ".join(
                f"{alias}={a.qualified_name}"
                for alias, a in self._artifacts.items()
            )
        else:
            alias_list = "-"
        return (
            f"TestRangeTest {self.qualified_name}\n"
            f"  srcs:      {', '.join(s.name for s in self.srcs)}\n"
            f"  factory:   {self.factory}\n"
            f"  artifacts: {alias_list}"
        )
