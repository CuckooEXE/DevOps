"""PythonWheel.lint_cmds → black --check + ruff check wrappers."""

from __future__ import annotations

from pathlib import Path

from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.python import PythonWheel


def _ctx(tmp: Path) -> BuildContext:
    return BuildContext(workspace_root=tmp, build_dir=tmp / "build", profile=OptimizationLevel.Debug)


def test_python_wheel_lint_emits_black_and_ruff(tmp_project, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (tmp_path / "mod.py").write_text("x = 1\n")
    _, enter = tmp_project
    with enter():
        wheel = PythonWheel(name="x", srcs=[tmp_path / "mod.py"])
    cmds = wheel.lint_cmds(_ctx(tmp_path))
    labels = [c.label for c in cmds]
    assert any("black" in lbl for lbl in labels)
    assert any("ruff" in lbl for lbl in labels)


def test_python_wheel_lint_falls_back_to_project_root_when_no_srcs(tmp_project, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    _, enter = tmp_project
    with enter():
        wheel = PythonWheel(name="x", srcs=None)
    cmds = wheel.lint_cmds(_ctx(tmp_path))
    # Each lint command's argv ends with the project root since no srcs were given
    for c in cmds:
        assert str(tmp_path) in c.argv
