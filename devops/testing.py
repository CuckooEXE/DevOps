"""Test helpers for plugin authors.

Keeps plugin test files short and framework-internal imports away
from plugin code:

    from devops.testing import make_ctx, active_project

    def test_my_rust_binary(tmp_path):
        proj = Project(name="t", root=tmp_path)
        with active_project(proj):
            t = RustBinary(name="mycli", srcs=[tmp_path / "src/main.rs"])
        ctx = make_ctx(tmp_path)
        cmds = t.build_cmds(ctx)
        assert cmds[0].argv[0] == "cargo"
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from devops import registry
from devops.context import HOST_ARCH, BuildContext, Toolchain
from devops.core.command import Command
from devops.core.target import Project
from devops.options import OptimizationLevel


def make_ctx(
    tmp_path: Path,
    *,
    profile: OptimizationLevel = OptimizationLevel.Debug,
    toolchain: Toolchain | None = None,
) -> BuildContext:
    """Construct a minimal BuildContext for command-shape assertions.

    Plugin tests typically only exercise ``target.build_cmds(ctx)`` or
    equivalent — they don't need toolchains configured. Pass a custom
    ``toolchain=`` when the plugin's Target reads from ``extras``.
    """
    tc = toolchain or Toolchain()
    return BuildContext(
        workspace_root=tmp_path,
        build_dir=tmp_path / "build",
        profile=profile,
        toolchain=tc,
        toolchains={HOST_ARCH: tc},
    )


@contextmanager
def active_project(project: Project) -> Iterator[Project]:
    """Re-exported from ``devops.registry`` for plugin ergonomics."""
    with registry.active_project(project) as p:
        yield p


def assert_command_shape(
    cmds: list[Command],
    *,
    argv_contains: list[str] | None = None,
    outputs: list[Path] | None = None,
    inputs_include: list[Path] | None = None,
) -> None:
    """Readable asserter for build_cmds test cases.

    Each clause is optional. The first Command in ``cmds`` is
    inspected — if the plugin emits multiple, test them individually.
    """
    assert cmds, "expected at least one Command"
    cmd = cmds[0]
    if argv_contains is not None:
        for needle in argv_contains:
            assert needle in cmd.argv, f"{needle!r} not in {cmd.argv!r}"
    if outputs is not None:
        for o in outputs:
            assert o in cmd.outputs, f"{o} not in {cmd.outputs}"
    if inputs_include is not None:
        for p in inputs_include:
            assert p in cmd.inputs, f"{p} not in {cmd.inputs}"


__all__ = [
    "active_project",
    "assert_command_shape",
    "make_ctx",
]
