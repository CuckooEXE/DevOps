"""Example devops plugin: ``TarballArtifact``.

Bundles a set of srcs into a gzipped tarball using the system ``tar``
binary. Serves as the smallest end-to-end demonstration of the plugin
API: a new Artifact class, a tool registered via extras, and an
entry-point callable that installs both.

Usage in a consuming project's build.py::

    from builder import glob
    from builder.plugins import TarballArtifact

    TarballArtifact(
        name="release",
        srcs=glob("dist/**/*.so") + ["README.md"],
        doc="All shared libs + the release notes.",
    )

Then::

    pip install -e ./plugins/devops-example-tarball
    devops build release           # → build/Debug/host/<proj>/release/release.tar.gz
"""

from __future__ import annotations

from pathlib import Path

from devops.api import Artifact, BuildContext, Command, Tool


MIN_API_VERSION = "1"


class TarballArtifact(Artifact):
    """Bundle files into a .tar.gz using ``tar``."""

    def __init__(
        self,
        name: str,
        srcs: list[str | Path],
        *,
        deps: dict | None = None,
        doc: str | None = None,
    ) -> None:
        super().__init__(name=name, deps=deps, doc=doc)
        # Resolve sources relative to the active project's root so users
        # can pass short names ("README.md") or absolute paths.
        self.srcs: tuple[Path, ...] = tuple(
            p if (p := Path(s)).is_absolute() else self.project.root / s
            for s in srcs
        )

    def build_cmds(self, ctx: BuildContext) -> list[Command]:
        if "tar" not in ctx.toolchain_for(self.arch).extras:
            raise RuntimeError(
                f"TarballArtifact {self.name!r}: no 'tar' tool configured. "
                f"Install the devops-example-tarball plugin or add "
                f"[toolchain.extras]\\ntar = \"tar\" to devops.toml."
            )
        tar = ctx.toolchain_for(self.arch).extras["tar"]
        out = self.output_path(ctx)
        # `tar -czf <out> -C <proj-root> <paths relative to proj-root>`
        # keeps the archive rooted at the project so the user gets
        # familiar relative paths inside the tarball. Every src must
        # live under the project root — reject early with a clear
        # error rather than letting relative_to raise.
        rel_srcs: list[str] = []
        for s in self.srcs:
            try:
                rel_srcs.append(str(s.relative_to(self.project.root)))
            except ValueError as e:
                raise ValueError(
                    f"TarballArtifact {self.name!r}: src {s} is outside "
                    f"the project root {self.project.root} — only "
                    f"in-project files can be tarred."
                ) from e
        return [
            Command.shell_cmd(
                f"mkdir -p {out.parent}",
                label=f"prepare {self.name}",
            ),
            Command(
                argv=tar.invoke(["-czf", str(out), "-C", str(self.project.root), *rel_srcs]),
                cwd=self.project.root,
                label=f"tar {self.name}",
                inputs=self.srcs,
                outputs=(out,),
            ),
        ]

    def output_path(self, ctx: BuildContext) -> Path:
        return self.output_dir(ctx) / f"{self.name}.tar.gz"

    def describe(self) -> str:
        return (
            f"TarballArtifact {self.qualified_name}\n"
            f"  srcs: {', '.join(s.name for s in self.srcs)}"
        )


def register(api) -> None:
    """Entry-point hook called once at ``builder`` import time."""
    api.register_target(TarballArtifact)
    api.DEFAULT_TOOLCHAIN_EXTRAS.setdefault("tar", Tool.of("tar"))
