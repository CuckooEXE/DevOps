"""ZigBinary: command shape + profile→optimize mode + rejection of bad paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.zig import ZigBinary


def _ctx(tmp: Path, profile: OptimizationLevel = OptimizationLevel.Debug) -> BuildContext:
    return BuildContext(workspace_root=tmp, build_dir=tmp / "build", profile=profile)


def _seed_zig_project(tmp: Path, subdir: str = "zigproj") -> Path:
    root = tmp / subdir
    root.mkdir()
    (root / "build.zig").write_text("// stub\n")
    return root


def test_zigbinary_rejects_missing_project_dir(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        with pytest.raises(FileNotFoundError, match="project_dir"):
            ZigBinary(name="z", project_dir="does_not_exist")


def test_zigbinary_rejects_missing_build_zig(tmp_project, tmp_path):
    (tmp_path / "zigproj").mkdir()
    _, enter = tmp_project
    with enter():
        with pytest.raises(FileNotFoundError, match="build.zig"):
            ZigBinary(name="z", project_dir="zigproj")


def test_zigbinary_output_path_is_bin_exe(tmp_project, tmp_path):
    _seed_zig_project(tmp_path)
    _, enter = tmp_project
    with enter():
        z = ZigBinary(name="z", project_dir="zigproj")
    out = z.output_path(_ctx(tmp_path))
    assert out.parts[-2:] == ("bin", "z")


def test_zigbinary_output_path_uses_explicit_exe(tmp_project, tmp_path):
    _seed_zig_project(tmp_path)
    _, enter = tmp_project
    with enter():
        z = ZigBinary(name="target", project_dir="zigproj", exe="my-real-binary")
    out = z.output_path(_ctx(tmp_path))
    assert out.name == "my-real-binary"


def test_zigbinary_build_cmd_has_prefix_and_optimize(tmp_project, tmp_path):
    _seed_zig_project(tmp_path)
    _, enter = tmp_project
    with enter():
        z = ZigBinary(name="z", project_dir="zigproj")
    cmd = z.build_cmds(_ctx(tmp_path))[0]
    assert "build" in cmd.argv
    assert "--prefix" in cmd.argv
    assert "-Doptimize=Debug" in cmd.argv


def test_zigbinary_profile_maps_optimize_mode(tmp_project, tmp_path):
    _seed_zig_project(tmp_path)
    _, enter = tmp_project
    with enter():
        z = ZigBinary(name="z", project_dir="zigproj")
    # Release -> ReleaseFast
    argv = z.build_cmds(_ctx(tmp_path, OptimizationLevel.Release))[0].argv
    assert "-Doptimize=ReleaseFast" in argv
    # ReleaseSafe stays ReleaseSafe
    argv = z.build_cmds(_ctx(tmp_path, OptimizationLevel.ReleaseSafe))[0].argv
    assert "-Doptimize=ReleaseSafe" in argv


def test_zigbinary_extra_args_appended(tmp_project, tmp_path):
    _seed_zig_project(tmp_path)
    _, enter = tmp_project
    with enter():
        z = ZigBinary(name="z", project_dir="zigproj", zig_args=("-Dextra=42",))
    argv = z.build_cmds(_ctx(tmp_path))[0].argv
    assert "-Dextra=42" in argv


def test_zigbinary_lint_uses_zig_fmt_check(tmp_project, tmp_path):
    _seed_zig_project(tmp_path)
    _, enter = tmp_project
    with enter():
        z = ZigBinary(name="z", project_dir="zigproj")
    cmd = z.lint_cmds(_ctx(tmp_path))[0]
    assert "fmt" in cmd.argv
    assert "--check" in cmd.argv
