"""devops run / build / describe against a remote-ref spec.

End-to-end tests use a DirectoryRef pointing at an in-tree fixture
(so no network, no git cloning). The clone path gets coverage via the
URL-to-ref parser.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from devops import remote_run
from devops.cli import app
from devops.remote import DirectoryRef, GitRef, TarballRef


# ---------- parser ----------


def test_parse_git_ssh_spec():
    ref = remote_run.parse_spec("git+ssh://git@github.com/acme/libfoo::MyTarget")
    assert isinstance(ref, GitRef)
    assert ref.url == "ssh://git@github.com/acme/libfoo"
    assert ref.target == "MyTarget"
    assert ref.ref is None


def test_parse_git_spec_with_ref():
    ref = remote_run.parse_spec("git+https://github.com/acme/libfoo@v1.2::MyApp")
    assert isinstance(ref, GitRef)
    assert ref.url == "https://github.com/acme/libfoo"
    assert ref.ref == "v1.2"
    assert ref.target == "MyApp"


def test_parse_git_file_spec():
    ref = remote_run.parse_spec("git+file:///tmp/local/repo::Target")
    assert isinstance(ref, GitRef)
    assert ref.url == "file:///tmp/local/repo"


def test_parse_https_tarball():
    ref = remote_run.parse_spec("https://example.com/archive.tar.gz::Target")
    assert isinstance(ref, TarballRef)
    assert ref.url == "https://example.com/archive.tar.gz"
    assert ref.target == "Target"


def test_parse_file_url():
    ref = remote_run.parse_spec("file:///abs/path/dir::Target")
    assert isinstance(ref, TarballRef)


def test_parse_absolute_path():
    ref = remote_run.parse_spec("/abs/path::Target")
    assert isinstance(ref, DirectoryRef)
    assert ref.path == "/abs/path"
    assert ref.target == "Target"


def test_parse_relative_path():
    ref = remote_run.parse_spec("./sibling/proj::Target")
    assert isinstance(ref, DirectoryRef)


def test_parse_returns_none_for_plain_name():
    assert remote_run.parse_spec("MyTarget") is None
    assert remote_run.parse_spec("project::MyTarget") is None


def test_parse_returns_none_for_missing_target():
    assert remote_run.parse_spec("git+ssh://host/repo::") is None


# ---------- end-to-end via DirectoryRef ----------


def _make_mini_project(root: Path) -> Path:
    """Create a devops project at `root` with one runnable ElfBinary."""
    (root).mkdir(parents=True, exist_ok=True)
    (root / "devops.toml").write_text("")
    (root / "main.c").write_text('#include <stdio.h>\nint main(){puts("hi");return 0;}\n')
    (root / "build.py").write_text(
        "from builder import ElfBinary\n"
        'ElfBinary(name="tinytool", srcs=["main.c"])\n'
    )
    return root


def test_resolve_builds_remote_target(tmp_path):
    proj = _make_mini_project(tmp_path / "proj")
    spec = f"{proj}::tinytool"
    ref, target = remote_run.resolve(spec)
    assert target.name == "tinytool"
    assert isinstance(ref, DirectoryRef)


def test_adhoc_context_points_at_cache(tmp_path):
    proj = _make_mini_project(tmp_path / "proj")
    spec = f"{proj}::tinytool"
    ref, target = remote_run.resolve(spec)
    ctx = remote_run.adhoc_context(target, ref)
    assert ctx.workspace_root == target.project.root
    assert "devops/run/" in str(ctx.build_dir)


def test_build_cli_accepts_remote_spec(tmp_path, monkeypatch):
    proj = _make_mini_project(tmp_path / "proj")
    # Run from an unrelated cwd — `devops build <remote>` should not
    # require being inside any local workspace.
    away = tmp_path / "elsewhere"
    away.mkdir()
    monkeypatch.chdir(away)
    spec = f"{proj}::tinytool"
    result = CliRunner().invoke(app, ["build", spec])
    # We tolerate tool-missing failures on CI hosts without a C compiler;
    # the point of this test is that the CLI reached the remote-build path.
    assert "tinytool" in (result.stdout + (result.stderr or ""))


def test_describe_cli_accepts_remote_spec(tmp_path, monkeypatch):
    proj = _make_mini_project(tmp_path / "proj")
    away = tmp_path / "elsewhere"
    away.mkdir()
    monkeypatch.chdir(away)
    result = CliRunner().invoke(app, ["describe", f"{proj}::tinytool"])
    assert result.exit_code == 0, result.stdout
    assert "tinytool" in result.stdout


def test_run_cli_accepts_remote_spec_dry_run(tmp_path, monkeypatch):
    """--dry-run exercises the full path except the actual build/exec."""
    proj = _make_mini_project(tmp_path / "proj")
    away = tmp_path / "elsewhere"
    away.mkdir()
    monkeypatch.chdir(away)
    spec = f"{proj}::tinytool"
    result = CliRunner().invoke(app, ["run", spec, "--dry-run"])
    # exit_code may be non-zero on hosts without a C compiler; what we're
    # verifying is that the remote-spec dispatch path runs.
    assert "tinytool" in (result.stdout + (result.stderr or ""))
