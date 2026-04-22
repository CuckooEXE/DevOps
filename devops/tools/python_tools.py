"""black --check, ruff check — python lint commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from devops.core.command import Command

if TYPE_CHECKING:
    from devops.context import BuildContext
    from devops.targets.python import PythonWheel


def lint_for_python(target: "PythonWheel", ctx: "BuildContext") -> list[Command]:
    project_root = target.project.root
    srcs = [str(s) for s in target.srcs] or [str(project_root)]

    black = ctx.toolchain.black.resolved_for(
        workspace=ctx.workspace_root, project=project_root, cwd=project_root
    )
    ruff = ctx.toolchain.ruff.resolved_for(
        workspace=ctx.workspace_root, project=project_root, cwd=project_root
    )
    return [
        Command(
            argv=black.invoke(["--check", *srcs]),
            cwd=project_root,
            label=f"black {target.name}",
        ),
        Command(
            argv=ruff.invoke(["check", *srcs]),
            cwd=project_root,
            label=f"ruff {target.name}",
        ),
    ]
