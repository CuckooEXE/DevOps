"""CLI-level integration: run subcommands through typer's CliRunner
against an ephemeral workspace to verify the user-visible surface."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from devops.cli import app


BUILD_PY = """
from builder import ElfBinary, Script, glob

app = ElfBinary(
    name="hello",
    srcs=glob("main.c"),
    doc="Prints hello world.",
)

Script(
    name="saybye",
    cmds=["echo bye"],
    doc="Says bye.",
)
"""


def _make_workspace(tmp_path: Path) -> Path:
    (tmp_path / "devops.toml").write_text("")
    (tmp_path / "main.c").write_text(
        "#include <stdio.h>\nint main(){puts(\"hi\"); return 0;}"
    )
    (tmp_path / "build.py").write_text(BUILD_PY)
    return tmp_path


def test_describe_lists_targets_with_docs(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(app, ["describe"])
    assert result.exit_code == 0, result.stdout
    assert "hello" in result.stdout
    assert "saybye" in result.stdout
    assert "Prints hello world." in result.stdout


def test_cmds_prints_build_commands(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(app, ["cmds", "hello"])
    assert result.exit_code == 0
    assert "main.c" in result.stdout


def test_build_produces_expected_artifact(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(app, ["build", "hello"])
    assert result.exit_code == 0, result.stdout
    artifact = ws / "build" / "Debug" / ws.name / "hello" / "hello"
    assert artifact.is_file()


def test_run_executes_artifact(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(app, ["run", "hello"])
    # subprocess stdout is not captured by CliRunner; just assert exit_code
    assert result.exit_code == 0, result.stdout


def test_run_script_via_dry_run(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(app, ["run", "saybye", "--dry-run"])
    assert result.exit_code == 0
    assert "echo bye" in result.stdout


def test_version_errors_without_name(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(app, ["version"])
    # Error path: exit code 1; message goes to stderr, not captured by older
    # CliRunner versions — so only assert the exit code.
    assert result.exit_code == 1


def test_version_with_name(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(app, ["version", "hello"])
    assert result.exit_code == 0


def test_unknown_target_reports_nicely(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(app, ["build", "no_such_target"])
    assert result.exit_code != 0


def test_clean_removes_output(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path)
    monkeypatch.chdir(ws)
    assert CliRunner().invoke(app, ["build", "hello"]).exit_code == 0
    artifact = ws / "build" / "Debug" / ws.name / "hello" / "hello"
    assert artifact.is_file()
    assert CliRunner().invoke(app, ["clean", "hello"]).exit_code == 0
    assert not artifact.is_file()
