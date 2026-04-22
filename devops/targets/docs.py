"""SphinxDocs — builds an HTML docs site via sphinx-build."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from devops.core.command import Command
from devops.core.target import Artifact, Target
from devops.targets.c_cpp import SourcesSpec, _resolve_sources

if TYPE_CHECKING:
    from devops.context import BuildContext


class SphinxDocs(Artifact):
    def __init__(
        self,
        name: str,
        srcs: SourcesSpec,
        conf: str | Path = "docs",
        deps: dict[str, Target] | None = None,
        version: str | None = None,
        doc: str | None = None,
    ) -> None:
        super().__init__(name=name, deps=deps, version=version, doc=doc)
        self.srcs = _resolve_sources(self.project.root, srcs)
        self.conf_dir = self.project.root / conf

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / "html"

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        sphinx = ctx.toolchain.sphinx_build.resolved_for(
            workspace=ctx.workspace_root, project=self.project.root, cwd=self.project.root
        )
        out = self.output_path(ctx)
        return [
            Command(
                argv=sphinx.invoke(["-b", "html", str(self.conf_dir), str(out)]),
                cwd=self.project.root,
                label=f"sphinx {self.name}",
                inputs=tuple(self.srcs),
                outputs=(out,),
            )
        ]

    def lint_cmds(self, ctx: "BuildContext") -> list[Command]:
        sphinx = ctx.toolchain.sphinx_build.resolved_for(
            workspace=ctx.workspace_root, project=self.project.root, cwd=self.project.root
        )
        out = self.output_dir(ctx) / "_lint_html"
        # -Q: quiet, only warnings/errors to stderr
        # -W: warnings become errors
        # -n: nitpicky mode (flag missing cross-references)
        return [
            Command(
                argv=sphinx.invoke(["-Q", "-W", "-n", "-b", "html", str(self.conf_dir), str(out)]),
                cwd=self.project.root,
                label=f"sphinx-lint {self.name}",
                inputs=tuple(self.srcs),
            )
        ]

    def describe(self) -> str:
        return (
            f"SphinxDocs {self.qualified_name}\n"
            f"  conf: {self.conf_dir}\n"
            f"  srcs: {len(self.srcs)} file(s)"
        )
