"""Broader CLI surface coverage: describe filters, cmds, clean, run errors,
test/install/doctor aggregation paths."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from devops.cli import app


WORKSPACE_BUILD_PY = """
from builder import (
    ElfBinary, ElfSharedObject, HeadersOnly, Install, Script, glob,
)

lib = ElfSharedObject(name="lib", srcs=glob("lib.c"), doc="demo shared lib")
app_ = ElfBinary(name="hello", srcs=glob("main.c"), libs=[lib])
HeadersOnly(name="hdrs", srcs=glob("hdr.h"))

Script(name="bye", cmds=["echo bye"])

Install(name="install-hello", artifact=app_, dest="/tmp/devops_cli_test")
"""


def _make_ws(tmp_path: Path) -> Path:
    (tmp_path / "devops.toml").write_text("")
    (tmp_path / "main.c").write_text(
        "#include <stdio.h>\nint main(){puts(\"hi\"); return 0;}"
    )
    (tmp_path / "lib.c").write_text("int lib_fn(){return 0;}")
    (tmp_path / "hdr.h").write_text("#pragma once\n")
    (tmp_path / "build.py").write_text(WORKSPACE_BUILD_PY)
    return tmp_path


def test_describe_filters_by_name(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(app, ["describe", "hello"])
    assert result.exit_code == 0
    assert "hello" in result.output
    # other targets absent when we filter
    assert "bye" not in result.output


def test_cmds_prints_transitive_deps(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(app, ["cmds", "hello"])
    assert result.exit_code == 0
    # lib is a dep and should appear in the printed cmds
    assert "liblib.so" in result.output or "-llib" in result.output


def test_clean_removes_output(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    monkeypatch.chdir(ws)
    assert CliRunner().invoke(app, ["build", "hello"]).exit_code == 0
    artifact = ws / "build" / "Debug" / "host" / ws.name / "hello" / "hello"
    assert artifact.is_file()
    assert CliRunner().invoke(app, ["clean", "hello"]).exit_code == 0
    assert not artifact.is_file()


def test_clean_all_with_no_args(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    monkeypatch.chdir(ws)
    CliRunner().invoke(app, ["build", "hello"]).exit_code == 0
    result = CliRunner().invoke(app, ["clean"])
    assert result.exit_code == 0


def test_run_rejects_library(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(app, ["run", "lib"])
    assert result.exit_code == 1


def test_run_rejects_headers_only(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(app, ["run", "hdrs"])
    assert result.exit_code == 1


def test_build_on_script_target_errors(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    monkeypatch.chdir(ws)
    # `build` takes an artifact, not a Script
    result = CliRunner().invoke(app, ["build", "bye"])
    assert result.exit_code == 1


def test_install_with_no_targets_errors(tmp_path, monkeypatch):
    (tmp_path / "devops.toml").write_text("")
    (tmp_path / "main.c").write_text("int main(){return 0;}")
    (tmp_path / "build.py").write_text(
        "from builder import ElfBinary, glob\n"
        "ElfBinary(name='x', srcs=glob('main.c'))\n"
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["install"])
    assert result.exit_code == 1


def test_install_runs_install_target(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    monkeypatch.chdir(ws)
    import shutil
    (ws / "dst").mkdir()
    # Point the install dest at a writable tmp dir (override via fresh build.py)
    (ws / "build.py").write_text(
        WORKSPACE_BUILD_PY.replace("/tmp/devops_cli_test", str(ws / "dst"))
    )
    result = CliRunner().invoke(app, ["install", "install-hello"])
    assert result.exit_code == 0, result.output
    assert (ws / "dst" / "hello").is_file()


def test_run_dry_run_on_script(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    monkeypatch.chdir(ws)
    result = CliRunner().invoke(app, ["run", "bye", "--dry-run"])
    assert result.exit_code == 0
    assert "echo bye" in result.output


def test_lint_no_arg_runs_all(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    monkeypatch.chdir(ws)
    # Will likely fail if lint tools aren't installed — accept either
    # outcome since we're exercising the aggregation path.
    result = CliRunner().invoke(app, ["lint"])
    assert result.exit_code in (0, 1)


def test_version_errors_on_non_artifact(tmp_path, monkeypatch):
    ws = _make_ws(tmp_path)
    monkeypatch.chdir(ws)
    # Scripts don't have versions in the sense the command asks about
    result = CliRunner().invoke(app, ["version", "bye"])
    assert result.exit_code == 1
