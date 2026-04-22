"""clang-tidy, clang-format, cppcheck wrappers for CCompile artifacts.

Each tool pulls the compile flag vector straight from the artifact via
`_compile_flags(ctx)` — the same flags the build uses. No restatement.

Tools are invoked through `ctx.toolchain.<tool>` so a Docker-wrapped
clang-tidy (same container as cc) Just Works.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from devops.core.command import Command

if TYPE_CHECKING:
    from devops.context import BuildContext
    from devops.targets.c_cpp import CCompile


def _cppcheck_flags_from_compile(compile_flags: tuple[str, ...]) -> list[str]:
    """cppcheck accepts -I / -D / -U. Filter the rest out."""
    kept: list[str] = []
    for f in compile_flags:
        if f.startswith(("-I", "-D", "-U")):
            kept.append(f)
    return kept


def lint_for_ccompile(target: "CCompile", ctx: "BuildContext") -> list[Command]:
    from devops.targets.c_cpp import CCompile  # noqa: F401

    compile_flags = target._compile_flags(ctx)
    project_root = target.project.root  # type: ignore[attr-defined]
    cmds: list[Command] = []

    # clang-format --dry-run --Werror (reads .clang-format if present)
    fmt = ctx.toolchain.clang_format.resolved_for(
        workspace=ctx.workspace_root, project=project_root, cwd=project_root
    )
    cmds.append(
        Command(
            argv=fmt.invoke([
                "--dry-run", "--Werror",
                *(str(s) for s in target.srcs),
            ]),
            cwd=project_root,
            label=f"clang-format {target.name}",
            inputs=tuple(target.srcs),
        )
    )

    # clang-tidy <src> -- <compile_flags>
    tidy = ctx.toolchain.clang_tidy.resolved_for(
        workspace=ctx.workspace_root, project=project_root, cwd=project_root
    )
    for src in target.srcs:
        cmds.append(
            Command(
                argv=tidy.invoke([str(src), "--", *compile_flags]),
                cwd=project_root,
                label=f"clang-tidy {src.name}",
                inputs=(src,),
            )
        )

    # cppcheck (only -I/-D/-U consumed)
    cppcheck = ctx.toolchain.cppcheck.resolved_for(
        workspace=ctx.workspace_root, project=project_root, cwd=project_root
    )
    cmds.append(
        Command(
            argv=cppcheck.invoke([
                "--error-exitcode=1",
                "--enable=warning,style,performance,portability",
                "--quiet",
                *_cppcheck_flags_from_compile(compile_flags),
                *(str(s) for s in target.srcs),
            ]),
            cwd=project_root,
            label=f"cppcheck {target.name}",
            inputs=tuple(target.srcs),
        )
    )

    return cmds
