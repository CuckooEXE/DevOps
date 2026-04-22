"""ZigTest delegates to `zig build test`."""

from __future__ import annotations

from pathlib import Path

import pytest

from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.zig import ZigTest


def _ctx(tmp: Path, profile: OptimizationLevel = OptimizationLevel.Debug) -> BuildContext:
    return BuildContext(workspace_root=tmp, build_dir=tmp / "build", profile=profile)


def _seed_zig_project(tmp: Path, subdir: str = "zigproj") -> Path:
    root = tmp / subdir
    root.mkdir()
    (root / "build.zig").write_text("// stub build.zig\n")
    return root


def test_zigtest_rejects_missing_project_dir(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        with pytest.raises(FileNotFoundError, match="project_dir"):
            ZigTest(name="t", project_dir="nope")


def test_zigtest_rejects_missing_build_zig(tmp_project, tmp_path):
    (tmp_path / "z").mkdir()
    _, enter = tmp_project
    with enter():
        with pytest.raises(FileNotFoundError, match="build.zig"):
            ZigTest(name="t", project_dir="z")


def test_zigtest_build_cmds_are_empty(tmp_project, tmp_path):
    _seed_zig_project(tmp_path)
    _, enter = tmp_project
    with enter():
        t = ZigTest(name="t", project_dir="zigproj")
    assert t.build_cmds(_ctx(tmp_path)) == []


def test_zigtest_test_cmd_invokes_zig_build_test(tmp_project, tmp_path):
    _seed_zig_project(tmp_path)
    _, enter = tmp_project
    with enter():
        t = ZigTest(name="t", project_dir="zigproj")
    cmd = t.test_cmds(_ctx(tmp_path))[0]
    assert cmd.argv[0].endswith("zig")
    assert "build" in cmd.argv
    assert "test" in cmd.argv


def test_zigtest_passes_optimize_mode(tmp_project, tmp_path):
    _seed_zig_project(tmp_path)
    _, enter = tmp_project
    with enter():
        t = ZigTest(name="t", project_dir="zigproj")
    argv = t.test_cmds(_ctx(tmp_path, OptimizationLevel.ReleaseSafe))[0].argv
    assert "-Doptimize=ReleaseSafe" in argv


def test_zigtest_extra_args_appended(tmp_project, tmp_path):
    _seed_zig_project(tmp_path)
    _, enter = tmp_project
    with enter():
        t = ZigTest(name="t", project_dir="zigproj", zig_args=("--summary", "all"))
    argv = t.test_cmds(_ctx(tmp_path))[0].argv
    assert "--summary" in argv
    assert "all" in argv


def test_zigtest_is_host_arch(tmp_project, tmp_path):
    _seed_zig_project(tmp_path)
    _, enter = tmp_project
    with enter():
        t = ZigTest(name="t", project_dir="zigproj")
    assert t.arch == "host"
