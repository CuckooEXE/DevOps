"""PythonWheel — builds a wheel via `python -m build --wheel`."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from devops.core.command import Command
from devops.core.target import Artifact, Target
from devops.targets.c_cpp import _resolve_sources

if TYPE_CHECKING:
    from devops.context import BuildContext


class PythonWheel(Artifact):
    """Produces dist/<name>-<version>-py3-none-any.whl via `python -m build`.

    Expects a pyproject.toml in the project root (or `pyproject=` override).
    The tests= kwarg desugars to a Pytest target named "<name>Tests".
    """

    def __init__(
        self,
        name: str,
        srcs: str | list[str] | None = None,
        pyproject: str | Path = "pyproject.toml",
        tests: dict | None = None,
        version: str | None = None,
        deps: dict[str, Target] | None = None,
    ):
        super().__init__(name=name, deps=deps, version=version)
        self.srcs = _resolve_sources(self.project.root, srcs) if srcs else []
        self.pyproject = self.project.root / pyproject
        self._tests_spec = tests
        if tests is not None:
            from devops.targets.tests import Pytest

            Pytest(name=f"{name}Tests", target=self, **tests)

    def output_path(self, ctx: "BuildContext") -> Path:
        # Actual filename is "<dist-name>-<version>-py3-none-any.whl" where
        # <dist-name> comes from pyproject.toml. We don't parse it — return
        # the containing dir so downstream consumers glob for *.whl.
        return self.output_dir(ctx) / "dist"

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        python = ctx.toolchain.python.resolved_for(
            workspace=ctx.workspace_root, project=self.project.root, cwd=self.project.root
        )
        out_dir = self.output_path(ctx)
        return [
            Command(
                argv=python.invoke(["-m", "build", "--wheel", "--outdir", str(out_dir)]),
                cwd=self.project.root,
                label=f"build wheel {self.name}",
                inputs=(self.pyproject, *self.srcs),
                outputs=(out_dir,),
            )
        ]

    def lint_cmds(self, ctx: "BuildContext") -> list[Command]:
        from devops.tools import python_tools

        return python_tools.lint_for_python(self, ctx)

    def describe(self) -> str:
        return (
            f"PythonWheel {self.qualified_name}\n"
            f"  pyproject: {self.pyproject}\n"
            f"  srcs:      {len(self.srcs)} file(s)"
        )
