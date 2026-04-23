"""[bootstrap] section parsing + devops bootstrap command shape."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from devops.bootstrap import (
    BootstrapConfig,
    bootstrap_commands,
    load_bootstrap,
)
from devops.cli import app


# ---- parser -------------------------------------------------------------


def test_load_without_toml_returns_empty(tmp_path):
    cfg = load_bootstrap(tmp_path)
    assert cfg.is_empty


def test_load_with_no_bootstrap_section_returns_empty(tmp_path):
    (tmp_path / "devops.toml").write_text("[toolchain]\ncc='clang'\n")
    cfg = load_bootstrap(tmp_path)
    assert cfg.is_empty


def test_load_parses_full_section(tmp_path):
    (tmp_path / "devops.toml").write_text(
        "[bootstrap]\n"
        "apt = ['clang-19', 'cppcheck']\n"
        "pip = ['ruff==0.8.2', 'black']\n"
        "pip_args = ['--user', '--break-system-packages']\n"
        "run = ['sudo ln -sf /usr/bin/clang-19 /usr/local/bin/clang']\n"
    )
    cfg = load_bootstrap(tmp_path)
    assert cfg.apt == ("clang-19", "cppcheck")
    assert cfg.pip == ("ruff==0.8.2", "black")
    assert cfg.pip_args == ("--user", "--break-system-packages")
    assert len(cfg.run) == 1


def test_load_unknown_key_raises(tmp_path):
    (tmp_path / "devops.toml").write_text(
        "[bootstrap]\n"
        "apt = ['x']\n"
        "bogus = 'x'\n"
    )
    with pytest.raises(ValueError, match="unknown .bootstrap."):
        load_bootstrap(tmp_path)


def test_load_non_string_entry_raises(tmp_path):
    (tmp_path / "devops.toml").write_text(
        "[bootstrap]\n"
        "apt = [123]\n"
    )
    with pytest.raises(TypeError, match="must be a string"):
        load_bootstrap(tmp_path)


def test_load_scalar_string_becomes_single_element(tmp_path):
    (tmp_path / "devops.toml").write_text(
        "[bootstrap]\n"
        "apt = 'just-one'\n"
    )
    cfg = load_bootstrap(tmp_path)
    assert cfg.apt == ("just-one",)


def test_default_pip_args_used_when_absent(tmp_path):
    (tmp_path / "devops.toml").write_text(
        "[bootstrap]\n"
        "pip = ['ruff']\n"
    )
    cfg = load_bootstrap(tmp_path)
    assert cfg.pip_args == ("--user",)


# ---- bootstrap_commands shape ------------------------------------------


def test_empty_config_no_commands(tmp_path):
    cmds = bootstrap_commands(BootstrapConfig(), tmp_path)
    assert cmds == []


def test_apt_emits_update_then_install(tmp_path):
    cfg = BootstrapConfig(apt=("clang", "cppcheck"))
    cmds = bootstrap_commands(cfg, tmp_path)
    assert cmds[0].argv == ("sudo", "apt-get", "update")
    assert cmds[1].argv == ("sudo", "apt-get", "install", "-y", "clang", "cppcheck")


def test_pip_uses_python_m_pip(tmp_path):
    cfg = BootstrapConfig(pip=("ruff",), pip_args=("--user",))
    cmds = bootstrap_commands(cfg, tmp_path)
    assert cmds[0].argv == ("python3", "-m", "pip", "install", "--user", "ruff")


def test_run_lines_are_shell(tmp_path):
    cfg = BootstrapConfig(run=("echo hi",))
    cmds = bootstrap_commands(cfg, tmp_path)
    assert cmds[0].shell
    assert cmds[0].argv[0] == "echo hi"


def test_ordering_apt_then_pip_then_run(tmp_path):
    cfg = BootstrapConfig(
        apt=("a",),
        pip=("b",),
        run=("echo c",),
    )
    cmds = bootstrap_commands(cfg, tmp_path)
    # 2 apt (update + install) + 1 pip + 1 run = 4
    assert len(cmds) == 4
    labels = [c.label for c in cmds]
    assert labels[0] == "apt update"
    assert labels[1].startswith("apt install")
    assert labels[2].startswith("pip install")
    assert labels[3] == "bootstrap.run[0]"


# ---- CLI ----------------------------------------------------------------


def test_bootstrap_no_section_reports_nothing_to_do(tmp_path, monkeypatch):
    (tmp_path / "devops.toml").write_text("")
    (tmp_path / "build.py").write_text("")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["bootstrap"])
    assert result.exit_code == 0
    assert "nothing to do" in result.output


def test_bootstrap_dry_run_prints_commands(tmp_path, monkeypatch):
    (tmp_path / "devops.toml").write_text(
        "[bootstrap]\n"
        "apt = ['clang']\n"
    )
    (tmp_path / "build.py").write_text("")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["bootstrap", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "apt-get" in result.output
    assert "clang" in result.output


def test_bootstrap_verbose_lists_packages(tmp_path, monkeypatch):
    (tmp_path / "devops.toml").write_text(
        "[bootstrap]\n"
        "apt = ['clang-19', 'cppcheck']\n"
        "pip = ['ruff']\n"
    )
    (tmp_path / "build.py").write_text("")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["bootstrap", "--dry-run", "-v"])
    assert result.exit_code == 0
    assert "clang-19 cppcheck" in result.output
    assert "ruff" in result.output


def test_doctor_nudges_toward_bootstrap_when_configured(tmp_path, monkeypatch):
    (tmp_path / "devops.toml").write_text(
        "[bootstrap]\n"
        "apt = ['some-missing-tool']\n"
    )
    (tmp_path / "main.c").write_text("int main(){return 0;}")
    (tmp_path / "build.py").write_text(
        "from builder import CustomArtifact\n"
        "CustomArtifact(\n"
        "    name='x',\n"
        "    outputs=['f'],\n"
        "    cmds=['some-missing-tool > {out[0]}'],\n"
        "    required_tools=['some-missing-tool'],\n"
        ")\n"
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1
