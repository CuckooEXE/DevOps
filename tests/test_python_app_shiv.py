"""PythonApp venv + wrapper, use_venv=False, PythonShiv zipapp."""

from __future__ import annotations

from pathlib import Path

import pytest

from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.python import PythonApp, PythonShiv


def _ctx(tmp: Path) -> BuildContext:
    return BuildContext(
        workspace_root=tmp,
        build_dir=tmp / "build",
        profile=OptimizationLevel.Debug,
    )


# ----- PythonApp ---------------------------------------------------------


def test_python_app_default_requirements_detected(tmp_project, tmp_path):
    (tmp_path / "requirements.txt").write_text("click>=8\n")
    _, enter = tmp_project
    with enter():
        app = PythonApp(name="app", entry="mymod:main")
    assert app.requirements is not None
    assert app.requirements.name == "requirements.txt"


def test_python_app_without_requirements_txt(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        app = PythonApp(name="app", entry="mymod:main")
    assert app.requirements is None


def test_python_app_build_cmds_include_venv_and_wrapper(tmp_project, tmp_path):
    (tmp_path / "requirements.txt").write_text("click>=8\n")
    _, enter = tmp_project
    with enter():
        app = PythonApp(name="app", entry="mymod:main")
    cmds = app.build_cmds(_ctx(tmp_path))
    labels = [c.label for c in cmds]
    assert labels == ["venv app", "wrapper app"]


def test_python_app_use_venv_false_skips_venv(tmp_project, tmp_path):
    (tmp_path / "requirements.txt").write_text("click>=8\n")
    _, enter = tmp_project
    with enter():
        app = PythonApp(name="app", entry="mymod:main", use_venv=False)
    cmds = app.build_cmds(_ctx(tmp_path))
    # Only the wrapper step — no venv creation
    assert [c.label for c in cmds] == ["wrapper app"]


def test_python_app_use_venv_false_wrapper_uses_host_python(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        app = PythonApp(name="app", entry="mymod:main", use_venv=False)
    cmd = app.build_cmds(_ctx(tmp_path))[0]
    # The heredoc body should reference the host python, not a venv path.
    assert "python3" in cmd.argv[0] or "python" in cmd.argv[0]
    assert "venv/bin/python" not in cmd.argv[0]


def test_python_app_wrapper_has_module_entry_invocation(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        app = PythonApp(name="app", entry="mypkg.cli:main", use_venv=False)
    cmd = app.build_cmds(_ctx(tmp_path))[0]
    # The heredoc contains a python -c invocation importing mypkg.cli
    assert "from mypkg.cli import main as _f" in cmd.argv[0]


def test_python_app_wrapper_with_script_entry(tmp_project, tmp_path):
    (tmp_path / "run.py").write_text("print('hello')\n")
    _, enter = tmp_project
    with enter():
        app = PythonApp(name="app", entry="run.py", use_venv=False)
    cmd = app.build_cmds(_ctx(tmp_path))[0]
    assert "run.py" in cmd.argv[0]


def test_python_app_output_path_is_wrapper(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        app = PythonApp(name="app", entry="mymod:main")
    out = app.output_path(_ctx(tmp_path))
    assert out.name == "app"


def test_python_app_requirements_flow_into_inputs(tmp_project, tmp_path):
    reqs = tmp_path / "requirements.txt"
    reqs.write_text("click>=8\n")
    _, enter = tmp_project
    with enter():
        app = PythonApp(name="app", entry="mymod:main")
    venv_cmd = app.build_cmds(_ctx(tmp_path))[0]
    assert reqs in venv_cmd.inputs


def test_python_app_pyproject_gets_editable_install(tmp_project, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='p'\nversion='0'\n")
    _, enter = tmp_project
    with enter():
        app = PythonApp(name="app", entry="p:main", pyproject="pyproject.toml")
    venv_cmd = app.build_cmds(_ctx(tmp_path))[0]
    assert "pip install --quiet -e" in venv_cmd.argv[0]


# ----- PythonShiv --------------------------------------------------------


def test_python_shiv_requires_module_colon_func_entry(tmp_project, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='p'\nversion='0'\n")
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="module:function"):
            PythonShiv(name="a", entry="script.py", pyproject="pyproject.toml")


def test_python_shiv_requires_existing_pyproject(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        with pytest.raises(FileNotFoundError, match="pyproject.toml"):
            PythonShiv(name="a", entry="m:f", pyproject="nope.toml")


def test_python_shiv_output_is_pyz(tmp_project, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='p'\nversion='0'\n")
    _, enter = tmp_project
    with enter():
        shv = PythonShiv(name="myapp", entry="m:f", pyproject="pyproject.toml")
    out = shv.output_path(_ctx(tmp_path))
    assert out.name == "myapp.pyz"


def test_python_shiv_command_shape(tmp_project, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='p'\nversion='0'\n")
    (tmp_path / "requirements.txt").write_text("click\n")
    _, enter = tmp_project
    with enter():
        shv = PythonShiv(name="myapp", entry="m:f", pyproject="pyproject.toml")
    cmd = shv.build_cmds(_ctx(tmp_path))[0]
    assert "-o" in cmd.argv
    assert "-e" in cmd.argv
    assert "m:f" in cmd.argv
    assert "-r" in cmd.argv


def test_python_shiv_shebang_override(tmp_project, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='p'\nversion='0'\n")
    _, enter = tmp_project
    with enter():
        shv = PythonShiv(
            name="a", entry="m:f", pyproject="pyproject.toml",
            python_shebang="/usr/bin/env python3.12",
        )
    cmd = shv.build_cmds(_ctx(tmp_path))[0]
    assert "-p" in cmd.argv
    idx = list(cmd.argv).index("-p")
    assert cmd.argv[idx + 1] == "/usr/bin/env python3.12"
