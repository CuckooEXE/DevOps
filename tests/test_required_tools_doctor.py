"""required_tools kwarg + devops doctor preflight."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from devops.cli import app
from devops.context import BuildContext
from devops.core.target import Script
from devops.options import OptimizationLevel
from devops.targets.c_cpp import ElfBinary
from devops.targets.custom import CustomArtifact


def _ctx(tmp: Path) -> BuildContext:
    return BuildContext(workspace_root=tmp, build_dir=tmp / "build", profile=OptimizationLevel.Debug)


def _write(tmp: Path, rel: str, body: str = "") -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


# ---- required_tools kwarg plumbing --------------------------------------


def test_target_required_tools_stored(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        s = Script(name="s", cmds=["true"], required_tools=["strip", "objcopy"])
    assert s.required_tools == ("strip", "objcopy")


def test_target_required_tools_defaults_empty(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        s = Script(name="s", cmds=["true"])
    assert s.required_tools == ()


def test_custom_artifact_required_tools_kwarg(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        ca = CustomArtifact(
            name="stripit",
            outputs=["out"],
            cmds=["strip {out[0]}"],
            required_tools=["strip"],
        )
    assert "strip" in ca.required_tools


# ---- collect_tool_names auto-detection ----------------------------------


def test_elfbinary_auto_detects_clang(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        b = ElfBinary(name="app", srcs=[tmp_path / "main.c"])
    tools = b.collect_tool_names(_ctx(tmp_path))
    assert "clang" in tools


def test_custom_artifact_shell_commands_need_declaration(tmp_project, tmp_path):
    """Shell Commands hide their real executables — auto-detect skips them,
    so `required_tools=` is the only path."""
    _, enter = tmp_project
    with enter():
        undeclared = CustomArtifact(
            name="a",
            outputs=["f"],
            cmds=["strip {out[0]}"],   # no required_tools
        )
        declared = CustomArtifact(
            name="b",
            outputs=["f"],
            cmds=["strip {out[0]}"],
            required_tools=["strip"],
        )
    assert "strip" not in undeclared.collect_tool_names(_ctx(tmp_path))
    assert "strip" in declared.collect_tool_names(_ctx(tmp_path))


def test_collect_combines_declared_and_autodetected(tmp_project, tmp_path):
    """A Script with shell cmds: nothing auto-detected; all declared.
    This exercises the union logic end-to-end."""
    (tmp_path / "s.sh").write_text("#!/bin/sh\necho ok\n")
    _, enter = tmp_project
    with enter():
        s = Script(
            name="s",
            cmds=["tar xzf foo | gpg --verify -"],
            required_tools=["tar", "gpg"],
        )
    tools = s.collect_tool_names(_ctx(tmp_path))
    assert {"tar", "gpg"}.issubset(tools)


# ---- devops doctor CLI --------------------------------------------------


DOCTOR_BUILD_PY = """
from builder import ElfBinary, CustomArtifact, glob

app = ElfBinary(name="hello", srcs=glob("main.c"))

CustomArtifact(
    name="proc",
    inputs={"bin": app},
    outputs=["processed"],
    cmds=["some-vendor-tool {bin.output_path} > {out[0]}"],
    required_tools=["some-vendor-tool"],
)
"""


def _make_doctor_workspace(tmp_path: Path) -> Path:
    (tmp_path / "devops.toml").write_text("")
    (tmp_path / "main.c").write_text("int main(){return 0;}")
    (tmp_path / "build.py").write_text(DOCTOR_BUILD_PY)
    return tmp_path


def test_doctor_reports_missing_declared_tool(tmp_path, monkeypatch):
    ws = _make_doctor_workspace(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1  # missing some-vendor-tool


def test_doctor_passes_when_everything_present(tmp_path, monkeypatch):
    ws = _make_doctor_workspace(tmp_path)
    monkeypatch.chdir(ws)
    with patch("shutil.which", return_value="/usr/bin/fake"):
        result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "doctor ok" in result.output


def test_doctor_verbose_lists_every_tool(tmp_path, monkeypatch):
    ws = _make_doctor_workspace(tmp_path)
    monkeypatch.chdir(ws)
    with patch("shutil.which", return_value="/usr/bin/fake"):
        result = CliRunner().invoke(app, ["doctor", "-v"])
    assert result.exit_code == 0
    # Should list clang (auto-detected from ElfBinary) and some-vendor-tool
    # (declared on the CustomArtifact).
    assert "clang" in result.output
    assert "some-vendor-tool" in result.output


def test_doctor_filters_build_tree_paths(tmp_project, tmp_path, monkeypatch):
    """argv[0]s that point into our build tree (e.g. a test binary's own
    path) must not be reported as missing tools."""
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _write(tmp_path, "test.cc", "int main(){return 0;}")
    (tmp_path / "devops.toml").write_text("")
    (tmp_path / "build.py").write_text(
        "from builder import ElfBinary, GoogleTest, glob\n"
        "app = ElfBinary(name='app', srcs=glob('main.c'))\n"
        "GoogleTest(name='t', srcs=glob('test.cc'), target=app)\n"
    )
    monkeypatch.chdir(tmp_path)
    with patch("shutil.which", return_value="/usr/bin/fake"):
        result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
