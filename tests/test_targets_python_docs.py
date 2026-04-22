"""PythonWheel and SphinxDocs command shape."""

from __future__ import annotations

from pathlib import Path

from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.docs import SphinxDocs
from devops.targets.python import PythonWheel


def _ctx(tmp: Path) -> BuildContext:
    return BuildContext(workspace_root=tmp, build_dir=tmp / "build", profile=OptimizationLevel.Debug)


def test_pythonwheel_runs_python_m_build_from_pyproject_dir(tmp_project, tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    _, enter = tmp_project
    with enter():
        wheel = PythonWheel(name="x", pyproject="sub/pyproject.toml")
    cmd = wheel.build_cmds(_ctx(tmp_path))[0]
    assert ("-m", "build", "--wheel") == (
        cmd.argv[1], cmd.argv[2], cmd.argv[3]
    )
    assert "--outdir" in cmd.argv
    assert cmd.cwd == sub


def test_sphinxdocs_outputs_html_dir(tmp_project, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/conf.py").write_text("project='p'\nexclude_patterns=['_build']\n")
    (tmp_path / "docs/index.rst").write_text("x\n=\n")
    _, enter = tmp_project
    with enter():
        docs = SphinxDocs(
            name="docs",
            srcs=[tmp_path / "docs/index.rst", tmp_path / "docs/conf.py"],
            conf="docs",
        )
    cmd = docs.build_cmds(_ctx(tmp_path))[0]
    # sphinx-build -b html <conf> <out>
    assert "-b" in cmd.argv
    assert "html" in cmd.argv
    assert cmd.outputs[0].name == "html"


def test_sphinxdocs_lint_is_quiet_and_nitpicky(tmp_project, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/conf.py").write_text("project='p'\n")
    (tmp_path / "docs/index.rst").write_text("x\n=\n")
    _, enter = tmp_project
    with enter():
        docs = SphinxDocs(name="d", srcs=[tmp_path / "docs/index.rst"], conf="docs")
    lint_cmd = docs.lint_cmds(_ctx(tmp_path))[0]
    assert "-Q" in lint_cmd.argv  # quiet
    assert "-W" in lint_cmd.argv  # warnings as errors
    assert "-n" in lint_cmd.argv  # nitpicky
