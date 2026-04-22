"""Fill PythonApp gaps: requirements absent, python_deps resolution path,
wrapper edge cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.python import PythonApp, PythonShiv, PythonWheel, _resolve_python_dep


def _ctx(tmp: Path) -> BuildContext:
    return BuildContext(workspace_root=tmp, build_dir=tmp / "build", profile=OptimizationLevel.Debug)


def test_python_app_without_requirements_omits_pip_r(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        app = PythonApp(name="app", entry="m:f")
    venv_cmd = app.build_cmds(_ctx(tmp_path))[0]
    assert "pip install --quiet -r" not in venv_cmd.argv[0]


def test_python_app_no_pyproject_skips_editable_install(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        app = PythonApp(name="app", entry="m:f")
    venv_cmd = app.build_cmds(_ctx(tmp_path))[0]
    assert " -e " not in venv_cmd.argv[0]


def test_python_app_python_deps_flow_into_deps_dict(tmp_project, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='p'\nversion='0'\n")
    _, enter = tmp_project
    with enter():
        wheel = PythonWheel(name="w", pyproject="pyproject.toml")
        app = PythonApp(name="app", entry="m:f", python_deps=[wheel])
    assert wheel in app.deps.values()


def test_python_app_resolves_python_deps_to_wheel_builds(tmp_project, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='p'\nversion='0'\n")
    _, enter = tmp_project
    with enter():
        wheel = PythonWheel(name="w", pyproject="pyproject.toml")
        app = PythonApp(name="app", entry="m:f", python_deps=[wheel])
    cmds = app.build_cmds(_ctx(tmp_path))
    # Wheel build cmd prepended before venv/wrapper
    assert any("build wheel" in c.label for c in cmds)


def test_resolve_python_dep_rejects_bare_name(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="bare names"):
            _resolve_python_dep("just_a_name", None)


def test_resolve_python_dep_rejects_bad_type(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(TypeError):
            _resolve_python_dep(42, None)  # type: ignore[arg-type]


def test_python_shiv_with_python_deps_uses_shell_form(tmp_project, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='p'\nversion='0'\n")
    _, enter = tmp_project
    with enter():
        wheel = PythonWheel(name="w", pyproject="pyproject.toml")
        shv = PythonShiv(name="s", entry="m:f", pyproject="pyproject.toml", python_deps=[wheel])
    cmds = shv.build_cmds(_ctx(tmp_path))
    # Wheel build + shiv step
    assert any("build wheel" in c.label for c in cmds)
    shiv_cmd = [c for c in cmds if c.label.startswith("shiv")][0]
    assert shiv_cmd.shell  # shell-form so *.whl globs at run time


def test_python_shiv_without_python_deps_uses_argv_form(tmp_project, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='p'\nversion='0'\n")
    _, enter = tmp_project
    with enter():
        shv = PythonShiv(name="s", entry="m:f", pyproject="pyproject.toml")
    cmd = shv.build_cmds(_ctx(tmp_path))[0]
    assert not cmd.shell
