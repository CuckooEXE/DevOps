"""Remote reference resolution: file:// dir, file:// tarball, git+ssh://
split parsing, http(s):// dispatch, and end-to-end link wire-up."""

from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

import pytest

from devops import registry, remote
from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.c_cpp import ElfBinary, ElfSharedObject


@pytest.fixture(autouse=True)
def _isolated_remote_cache(tmp_path, monkeypatch):
    """Point CACHE_ROOT at a tmp dir and clear the in-process cache."""
    monkeypatch.setattr(remote, "CACHE_ROOT", tmp_path / "remotes")
    remote._reset_for_tests()
    yield


def _seed_remote_project(root: Path, libname: str = "remotelib") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "lib.c").write_text("int remotelib_f(){return 42;}\n")
    (root / "build.py").write_text(
        "from builder import ElfSharedObject, glob\n"
        f"ElfSharedObject(name={libname!r}, srcs=glob('lib.c'))\n"
    )


# ---- URL parsing --------------------------------------------------------


def test_spec_without_target_name_raises():
    with pytest.raises(ValueError, match="::<target-name>"):
        remote.resolve_remote_ref("file:///tmp/x")


def test_split_git_ref_handles_trailing_ref():
    base, ref = remote._split_git_ref("ssh://git@github.com/acme/repo@v1.2.3")
    assert base == "ssh://git@github.com/acme/repo"
    assert ref == "v1.2.3"


def test_split_git_ref_preserves_user_at_host():
    base, ref = remote._split_git_ref("ssh://git@github.com/acme/repo")
    assert base == "ssh://git@github.com/acme/repo"
    assert ref is None


def test_project_name_derived_from_path():
    assert remote._project_name_for("file:///a/b/libfoo", "abc") == "remote.libfoo"
    assert remote._project_name_for("file:///a/b/libfoo.tar.gz", "abc") == "remote.libfoo"
    assert remote._project_name_for("ssh://git@host/acme/repo.git", "abc") == "remote.repo"


# ---- file:// directory --------------------------------------------------


def test_file_url_points_at_directory(tmp_path):
    src = tmp_path / "remoteproj"
    _seed_remote_project(src)
    # No active local project — matches real link-time resolution
    t = remote.resolve_remote_ref(f"file://{src}::remotelib")
    assert isinstance(t, ElfSharedObject)
    assert t.name == "remotelib"


def test_file_url_rejects_non_existent(tmp_path):
    with pytest.raises(ValueError, match="file://"):
        remote.resolve_remote_ref(f"file://{tmp_path/'nope'}::x")


# ---- file:// tarball ----------------------------------------------------


def test_file_url_points_at_tarball(tmp_path):
    src = tmp_path / "remoteproj"
    _seed_remote_project(src)
    tarball = tmp_path / "remoteproj.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(src, arcname="remoteproj")

    t = remote.resolve_remote_ref(f"file://{tarball}::remotelib")
    assert isinstance(t, ElfSharedObject)


# ---- caching ------------------------------------------------------------


def test_same_url_resolves_once_per_process(tmp_path):
    src = tmp_path / "remoteproj"
    _seed_remote_project(src)
    a = remote.resolve_remote_ref(f"file://{src}::remotelib")
    b = remote.resolve_remote_ref(f"file://{src}::remotelib")
    assert a is b


def test_cache_dir_reused_across_calls(tmp_path):
    src = tmp_path / "remoteproj"
    _seed_remote_project(src)
    remote.resolve_remote_ref(f"file://{src}::remotelib")
    remote._reset_for_tests()
    registry.reset()
    # Second resolve uses the on-disk cache; still works
    t = remote.resolve_remote_ref(f"file://{src}::remotelib")
    assert t.name == "remotelib"


# ---- integration: ElfBinary linking a remote lib ------------------------


def test_elfbinary_links_remote_sharedobject(tmp_path, tmp_project):
    src = tmp_path / "remoteproj"
    _seed_remote_project(src)
    (tmp_path / "main.c").write_text("int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(
            name="app",
            srcs=[tmp_path / "main.c"],
            libs=[f"file://{src}::remotelib"],
        )
    # build_cmds runs AFTER discover_projects exits — no active project
    ctx = BuildContext(
        workspace_root=tmp_path,
        build_dir=tmp_path / "build",
        profile=OptimizationLevel.Debug,
    )
    link_cmd = app.build_cmds(ctx)[-1]
    assert "-lremotelib" in link_cmd.argv
    assert any(a.startswith("-L") and "remote.remoteproj" in a for a in link_cmd.argv)


# ---- git:// plumbing (parse-only — no network here) ---------------------


def test_git_ssh_url_split_strips_prefix_and_ref():
    # The function doesn't fetch — we're just exercising the parser path
    # via the internal split helper.
    url = "git+ssh://git@github.com/acme/libfoo@v1.2.3"
    stripped = url[len("git+"):]
    base, ref = remote._split_git_ref(stripped)
    assert base == "ssh://git@github.com/acme/libfoo"
    assert ref == "v1.2.3"


# ---- git+ssh:// against a real local bare repo (no network) -------------


def test_git_clone_against_local_bare_repo(tmp_path):
    """Set up a bare git repo locally, point git+file:// at it, confirm
    the fetch + build.py load flow works end-to-end."""
    work = tmp_path / "work"
    _seed_remote_project(work)
    subprocess.run(["git", "-C", str(work), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(work), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "seed"],
        check=True,
    )
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)

    t = remote.resolve_remote_ref(f"git+file://{bare}::remotelib")
    assert t.name == "remotelib"
