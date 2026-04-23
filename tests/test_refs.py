"""Typed remote references — GitRef / TarballRef / DirectoryRef.

Covers ``.to_spec()`` lowerings, the public ``resolve_remote_ref`` entry
point, end-to-end linking of a remote library, and the internal URL
parsing helpers (``_split_git_ref`` / ``_project_name_for``) the Refs
rely on for deriving cache keys and project names.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from devops import registry, remote
from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.remote import DirectoryRef, GitRef, Ref, TarballRef
from devops.targets.c_cpp import ElfBinary, ElfSharedObject


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


# ---- to_spec lowerings ---------------------------------------------------


def test_gitref_to_spec_with_ref():
    r = GitRef("ssh://git@github.com/acme/libfoo", target="mylib", ref="v1.2.3")
    assert r.to_spec() == "git+ssh://git@github.com/acme/libfoo@v1.2.3::mylib"


def test_gitref_to_spec_without_ref():
    r = GitRef("https://github.com/acme/libfoo.git", target="mylib")
    assert r.to_spec() == "git+https://github.com/acme/libfoo.git::mylib"


def test_tarballref_passes_through_http_url():
    r = TarballRef("https://example.com/pkg.tar.gz", target="mylib")
    assert r.to_spec() == "https://example.com/pkg.tar.gz::mylib"


def test_tarballref_rewrites_local_path_to_file_url(tmp_path):
    tarball = tmp_path / "pkg.tar.gz"
    tarball.write_bytes(b"")
    r = TarballRef(str(tarball), target="mylib")
    assert r.to_spec() == f"file://{tarball}::mylib"


def test_tarballref_resolves_relative_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    r = TarballRef("./rel/pkg.tar.gz", target="mylib")
    assert r.to_spec() == f"file://{(tmp_path / 'rel' / 'pkg.tar.gz').resolve()}::mylib"


def test_directoryref_absolute(tmp_path):
    r = DirectoryRef(str(tmp_path), target="mylib")
    assert r.to_spec() == f"file://{tmp_path}::mylib"


def test_directoryref_relative(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    r = DirectoryRef("./sub", target="mylib")
    assert r.to_spec() == f"file://{(tmp_path / 'sub').resolve()}::mylib"


def test_refs_are_frozen():
    r = DirectoryRef("/x", target="mylib")
    with pytest.raises(Exception):  # FrozenInstanceError subclasses AttributeError
        r.path = "/y"  # type: ignore[misc]


def test_typed_refs_are_ref_instances():
    assert isinstance(GitRef("ssh://h/x", target="t"), Ref)
    assert isinstance(TarballRef("https://h/x.tar.gz", target="t"), Ref)
    assert isinstance(DirectoryRef("/x", target="t"), Ref)


# ---- resolve_remote_ref entry point -------------------------------------


def test_resolve_rejects_non_ref_argument():
    with pytest.raises(TypeError, match="expects a Ref"):
        remote.resolve_remote_ref("file:///tmp/x::target")  # type: ignore[arg-type]


def test_resolve_rejects_empty_target_name(tmp_path):
    with pytest.raises(ValueError, match="missing a target name"):
        remote.resolve_remote_ref(DirectoryRef(str(tmp_path), target=""))


def test_resolve_accepts_directoryref(tmp_path):
    src = tmp_path / "remoteproj"
    _seed_remote_project(src)
    t = remote.resolve_remote_ref(DirectoryRef(str(src), target="remotelib"))
    assert isinstance(t, ElfSharedObject)
    assert t.name == "remotelib"


def test_resolve_directoryref_rejects_nonexistent(tmp_path):
    with pytest.raises(ValueError, match="file://"):
        remote.resolve_remote_ref(
            DirectoryRef(str(tmp_path / "nope"), target="x")
        )


def test_resolve_rejects_dir_without_build_py(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="build.py"):
        remote.resolve_remote_ref(DirectoryRef(str(empty), target="any"))


def test_resolve_accepts_tarballref_local_file(tmp_path):
    src = tmp_path / "remoteproj"
    _seed_remote_project(src)
    tarball = tmp_path / "remoteproj.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(src, arcname="remoteproj")

    t = remote.resolve_remote_ref(TarballRef(str(tarball), target="remotelib"))
    assert isinstance(t, ElfSharedObject)


def test_resolve_accepts_tarballref_over_http(tmp_path):
    """http(s) TarballRefs route through urlretrieve + tarball extraction."""
    src = tmp_path / "src"
    _seed_remote_project(src)
    tarball = tmp_path / "pkg.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(src, arcname="src")

    def fake_urlretrieve(url: str, path: str) -> None:
        shutil.copy(tarball, path)

    with patch("urllib.request.urlretrieve", fake_urlretrieve):
        t = remote.resolve_remote_ref(
            TarballRef("https://example.com/pkg.tar.gz", target="remotelib")
        )
    assert t.name == "remotelib"


def test_resolve_accepts_gitref_against_local_bare_repo(tmp_path):
    """No network: stand up a local bare repo and point GitRef at it via file://."""
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

    t = remote.resolve_remote_ref(GitRef(f"file://{bare}", target="remotelib"))
    assert t.name == "remotelib"


# ---- caching ------------------------------------------------------------


def test_same_ref_resolves_once_per_process(tmp_path):
    src = tmp_path / "remoteproj"
    _seed_remote_project(src)
    a = remote.resolve_remote_ref(DirectoryRef(str(src), target="remotelib"))
    b = remote.resolve_remote_ref(DirectoryRef(str(src), target="remotelib"))
    assert a is b


def test_on_disk_cache_is_reused_across_reset(tmp_path):
    src = tmp_path / "remoteproj"
    _seed_remote_project(src)
    remote.resolve_remote_ref(DirectoryRef(str(src), target="remotelib"))
    remote._reset_for_tests()
    registry.reset()
    # Second resolve uses the on-disk cache; still works
    t = remote.resolve_remote_ref(DirectoryRef(str(src), target="remotelib"))
    assert t.name == "remotelib"


# ---- Ref in libs= flows through ElfBinary link ---------------------------


def test_elfbinary_links_via_directoryref(tmp_path, tmp_project):
    src = tmp_path / "remoteproj"
    _seed_remote_project(src)
    (tmp_path / "main.c").write_text("int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(
            name="app",
            srcs=[tmp_path / "main.c"],
            libs=[DirectoryRef(str(src), target="remotelib")],
        )
    ctx = BuildContext(
        workspace_root=tmp_path,
        build_dir=tmp_path / "build",
        profile=OptimizationLevel.Debug,
    )
    link_cmd = app.build_cmds(ctx)[-1]
    assert "-lremotelib" in link_cmd.argv
    assert any(a.startswith("-L") and "remote.remoteproj" in a for a in link_cmd.argv)


def test_elfbinary_rejects_legacy_url_string(tmp_path, tmp_project):
    """A bare URL string in libs= is no longer accepted — must use a Ref."""
    (tmp_path / "main.c").write_text("int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(
            name="app",
            srcs=[tmp_path / "main.c"],
            libs=[f"file://{tmp_path}/x::remotelib"],
        )
    ctx = BuildContext(
        workspace_root=tmp_path,
        build_dir=tmp_path / "build",
        profile=OptimizationLevel.Debug,
    )
    with pytest.raises(TypeError, match="typed Ref"):
        app.build_cmds(ctx)


def test_elfbinary_describe_includes_ref_spec(tmp_path, tmp_project):
    src = tmp_path / "remoteproj"
    _seed_remote_project(src)
    (tmp_path / "main.c").write_text("int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(
            name="app",
            srcs=[tmp_path / "main.c"],
            libs=[DirectoryRef(str(src), target="remotelib")],
        )
    desc = app.describe()
    assert f"file://{src}::remotelib" in desc


# ---- internal parsing helpers (used by Ref.to_spec consumers) -----------


def test_split_git_ref_handles_trailing_ref():
    base, ref = remote._split_git_ref("ssh://git@github.com/acme/repo@v1.2.3")
    assert base == "ssh://git@github.com/acme/repo"
    assert ref == "v1.2.3"


def test_split_git_ref_preserves_user_at_host():
    base, ref = remote._split_git_ref("ssh://git@github.com/acme/repo")
    assert base == "ssh://git@github.com/acme/repo"
    assert ref is None


def test_split_git_ref_slash_before_at_means_no_ref():
    base, ref = remote._split_git_ref("ssh://git@host/org/repo@feature/branch")
    assert base == "ssh://git@host/org/repo@feature/branch"
    assert ref is None


def test_project_name_derived_from_path():
    assert remote._project_name_for("file:///a/b/libfoo", "abc") == "remote.libfoo"
    assert remote._project_name_for("file:///a/b/libfoo.tar.gz", "abc") == "remote.libfoo"
    assert remote._project_name_for("ssh://git@host/acme/repo.git", "abc") == "remote.repo"


def test_project_name_strips_git_suffix():
    assert remote._project_name_for("git+ssh://host/org/repo.git", "k") == "remote.repo"


# ---- build= override (local recipe for a source-only remote) -----------


def _seed_source_only(root: Path) -> None:
    """Remote source without a build.py — needs an external recipe."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "lib.c").write_text("int remotelib_f(){return 42;}\n")


def _write_recipe(path: Path, libname: str = "recipedLib") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from builder import ElfSharedObject, glob\n"
        f"ElfSharedObject(name={libname!r}, srcs=glob('lib.c'))\n"
    )


def test_directoryref_build_override_loads_local_recipe(tmp_path):
    src = tmp_path / "src"
    _seed_source_only(src)
    recipe = tmp_path / "recipes" / "mylib.build.py"
    _write_recipe(recipe, libname="recipedLib")
    t = remote.resolve_remote_ref(
        DirectoryRef(str(src), target="recipedLib", build=str(recipe))
    )
    assert isinstance(t, ElfSharedObject)
    assert t.name == "recipedLib"


def test_tarballref_build_override_loads_local_recipe(tmp_path):
    src = tmp_path / "src"
    _seed_source_only(src)
    tarball = tmp_path / "src.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(src, arcname="src")
    recipe = tmp_path / "recipes" / "mylib.build.py"
    _write_recipe(recipe, libname="recipedLib")
    t = remote.resolve_remote_ref(
        TarballRef(str(tarball), target="recipedLib", build=str(recipe))
    )
    assert t.name == "recipedLib"


def test_gitref_build_override_loads_local_recipe(tmp_path):
    work = tmp_path / "work"
    _seed_source_only(work)
    subprocess.run(["git", "-C", str(work), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(work), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "seed"],
        check=True,
    )
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    recipe = tmp_path / "recipes" / "mylib.build.py"
    _write_recipe(recipe, libname="recipedLib")

    t = remote.resolve_remote_ref(
        GitRef(f"file://{bare}", target="recipedLib", build=str(recipe))
    )
    assert t.name == "recipedLib"


def test_missing_build_override_raises(tmp_path):
    src = tmp_path / "src"
    _seed_source_only(src)
    with pytest.raises(FileNotFoundError, match="recipe not found"):
        remote.resolve_remote_ref(
            DirectoryRef(
                str(src), target="x", build=str(tmp_path / "nope.build.py")
            )
        )


def test_same_url_different_build_register_distinctly(tmp_path):
    """Two Refs sharing a URL but with different recipes must not clobber."""
    src = tmp_path / "src"
    _seed_source_only(src)
    recipe_a = tmp_path / "recipes" / "a.build.py"
    recipe_b = tmp_path / "recipes" / "b.build.py"
    _write_recipe(recipe_a, libname="libA")
    _write_recipe(recipe_b, libname="libB")

    ta = remote.resolve_remote_ref(
        DirectoryRef(str(src), target="libA", build=str(recipe_a))
    )
    tb = remote.resolve_remote_ref(
        DirectoryRef(str(src), target="libB", build=str(recipe_b))
    )
    assert ta.name == "libA"
    assert tb.name == "libB"
    assert ta.project.name != tb.project.name


def test_build_override_relative_path_resolves_from_cwd(tmp_path, monkeypatch):
    src = tmp_path / "src"
    _seed_source_only(src)
    recipe = tmp_path / "recipes" / "mylib.build.py"
    _write_recipe(recipe, libname="recipedLib")

    monkeypatch.chdir(tmp_path)
    t = remote.resolve_remote_ref(
        DirectoryRef(
            str(src), target="recipedLib", build="recipes/mylib.build.py"
        )
    )
    assert t.name == "recipedLib"


def test_recipe_glob_resolves_against_fetched_source(tmp_path):
    """A recipe's glob() must see the fetched source as the project root."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.c").write_text("int a(){return 1;}")
    (src / "b.c").write_text("int b(){return 2;}")
    recipe = tmp_path / "recipe.build.py"
    recipe.write_text(
        "from builder import ElfSharedObject, glob\n"
        "ElfSharedObject(name='bundled', srcs=glob('*.c'))\n"
    )
    t = remote.resolve_remote_ref(
        DirectoryRef(str(src), target="bundled", build=str(recipe))
    )
    assert isinstance(t, ElfSharedObject)
    # Both .c files discovered from the fetched source, not recipe's dir
    assert len(t.srcs) == 2
