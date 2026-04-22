"""Remote resolver edge cases: scheme dispatch, http routing, error paths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devops import remote


@pytest.fixture(autouse=True)
def _isolated_remote_cache(tmp_path, monkeypatch):
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


def test_unsupported_scheme_raises(tmp_path):
    with pytest.raises(ValueError, match="unsupported"):
        remote.resolve_remote_ref("ftp://example.com/path::target")


def test_missing_target_name_raises(tmp_path):
    with pytest.raises(ValueError, match="target name"):
        remote.resolve_remote_ref(f"file://{tmp_path}::")


def test_http_fetch_routes_through_tarball_extraction(tmp_path):
    import tarfile

    src = tmp_path / "src"
    _seed_remote_project(src)
    tarball = tmp_path / "pkg.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(src, arcname="src")

    # Stub urlretrieve so the http resolver copies from our tarball
    import shutil

    def fake_urlretrieve(url: str, path: str) -> None:
        shutil.copy(tarball, path)

    with patch("urllib.request.urlretrieve", fake_urlretrieve):
        t = remote.resolve_remote_ref(f"https://example.com/pkg.tar.gz::remotelib")
    assert t.name == "remotelib"


def test_missing_build_py_in_remote_raises(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="build.py"):
        remote.resolve_remote_ref(f"file://{empty}::any")


def test_relative_git_ref_is_extracted():
    base, ref = remote._split_git_ref("ssh://git@host/org/repo@feature/branch")
    assert base == "ssh://git@host/org/repo@feature/branch"  # `@` in segment with slash stays
    assert ref is None  # slash before @ means not a ref


def test_project_name_strips_git_suffix():
    assert remote._project_name_for("git+ssh://host/org/repo.git", "k") == "remote.repo"
