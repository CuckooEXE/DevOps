"""Ref support across artifact source kwargs.

Confirms that FileArtifact.src, DirectoryArtifact.src,
CompressedArtifact.entries values, CustomArtifact.inputs values, and
Install.artifact each accept a Ref and resolve it at build_cmds time
to the upstream Target's output_path.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from devops import remote
from devops.context import BuildContext
from devops.core import runner
from devops.options import OptimizationLevel
from devops.remote import DirectoryRef
from devops.targets.archive import CompressedArtifact, CompressionFormat
from devops.targets.copy import DirectoryArtifact, FileArtifact
from devops.targets.custom import CustomArtifact


@pytest.fixture(autouse=True)
def _isolated_remote_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "CACHE_ROOT", tmp_path / "remotes")
    remote._reset_for_tests()
    yield


def _ctx(tmp: Path) -> BuildContext:
    return BuildContext(
        workspace_root=tmp,
        build_dir=tmp / "build",
        profile=OptimizationLevel.Debug,
    )


def _seed_remote_file_target(root: Path) -> None:
    """Remote project whose `payload` CustomArtifact emits a single file."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "raw.txt").write_text("hello from remote\n")
    (root / "build.py").write_text(
        "from builder import CustomArtifact\n"
        "CustomArtifact(\n"
        "    name='payload',\n"
        "    inputs={'raw': 'raw.txt'},\n"
        "    outputs=['payload.txt'],\n"
        "    cmds=['cp {raw} {out[0]}'],\n"
        ")\n"
    )


# ---- FileArtifact: src=DirectoryRef -------------------------------------


def test_file_artifact_accepts_ref_source(tmp_project, tmp_path):
    remote_dir = tmp_path / "rem"
    _seed_remote_file_target(remote_dir)
    _, enter = tmp_project
    with enter():
        fa = FileArtifact(
            name="copied",
            src=DirectoryRef(str(remote_dir), target="payload"),
        )
    ctx = _ctx(tmp_path)
    runner.run_all(fa.build_cmds(ctx), use_cache=True)
    assert fa.output_path(ctx).read_text() == "hello from remote\n"


# ---- DirectoryArtifact: src=DirectoryRef --------------------------------


def _seed_remote_dir_target(root: Path) -> None:
    """Remote project whose `tree` HeadersOnly bundles a directory."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "include").mkdir()
    (root / "include" / "a.h").write_text("// a\n")
    (root / "include" / "b.h").write_text("// b\n")
    (root / "build.py").write_text(
        "from builder import HeadersOnly, glob\n"
        "HeadersOnly(name='tree', srcs=glob('include/*.h'), strip_prefix='include')\n"
    )


def test_directory_artifact_accepts_ref_source(tmp_project, tmp_path):
    remote_dir = tmp_path / "rem"
    _seed_remote_dir_target(remote_dir)
    _, enter = tmp_project
    with enter():
        da = DirectoryArtifact(
            name="bundled",
            src=DirectoryRef(str(remote_dir), target="tree"),
        )
    ctx = _ctx(tmp_path)
    # First, build the upstream — DirectoryArtifact relies on the
    # resolved Target's output existing on disk for its file walk.
    upstream = remote.resolve_remote_ref(
        DirectoryRef(str(remote_dir), target="tree")
    )
    runner.run_all(upstream.build_cmds(ctx), use_cache=False)  # type: ignore[attr-defined]
    runner.run_all(da.build_cmds(ctx), use_cache=False)
    out = da.output_path(ctx)
    assert (out / "a.h").exists()
    assert (out / "b.h").exists()


# ---- CompressedArtifact: entries={...: Ref} -----------------------------


def test_compressed_artifact_accepts_ref_entry(tmp_project, tmp_path):
    remote_dir = tmp_path / "rem"
    _seed_remote_file_target(remote_dir)
    _, enter = tmp_project
    with enter():
        ca = CompressedArtifact(
            name="bundle",
            format=CompressionFormat.TarGzip,
            entries={
                "remote/payload.txt": DirectoryRef(str(remote_dir), target="payload"),
            },
        )
    ctx = _ctx(tmp_path)
    upstream = remote.resolve_remote_ref(
        DirectoryRef(str(remote_dir), target="payload")
    )
    runner.run_all(upstream.build_cmds(ctx), use_cache=False)  # type: ignore[attr-defined]
    runner.run_all(ca.build_cmds(ctx), use_cache=False)
    out = ca.output_path(ctx)
    with tarfile.open(out, "r:gz") as tf:
        assert "remote/payload.txt" in tf.getnames()


# ---- CustomArtifact: inputs={...: Ref} ----------------------------------


def test_custom_artifact_accepts_ref_input(tmp_project, tmp_path):
    remote_dir = tmp_path / "rem"
    _seed_remote_file_target(remote_dir)
    _, enter = tmp_project
    with enter():
        ca = CustomArtifact(
            name="downstream",
            inputs={"upstream": DirectoryRef(str(remote_dir), target="payload")},
            outputs=["wrapped.txt"],
            cmds=["cp {upstream.output_path} {out[0]}"],
        )
    ctx = _ctx(tmp_path)
    upstream = remote.resolve_remote_ref(
        DirectoryRef(str(remote_dir), target="payload")
    )
    runner.run_all(upstream.build_cmds(ctx), use_cache=False)  # type: ignore[attr-defined]
    runner.run_all(ca.build_cmds(ctx), use_cache=False)
    assert ca.output_paths(ctx)[0].read_text() == "hello from remote\n"


# ---- Install: artifact=Ref ---------------------------------------------


def test_install_accepts_ref_artifact(tmp_project, tmp_path):
    """Install resolves the Ref at install_cmds time and dispatches on
    the resolved type. Use a remote ElfBinary so the install path
    materializes correctly without compiling."""
    from devops.targets.install import Install

    remote_dir = tmp_path / "rem"
    remote_dir.mkdir()
    (remote_dir / "main.c").write_text("int main(){return 0;}\n")
    (remote_dir / "build.py").write_text(
        "from builder import ElfBinary, glob\n"
        "ElfBinary(name='app', srcs=glob('main.c'))\n"
    )

    _, enter = tmp_project
    dest = tmp_path / "install_root"
    with enter():
        inst = Install(
            name="install_remote",
            artifact=DirectoryRef(str(remote_dir), target="app"),
            dest=dest,
        )
    ctx = _ctx(tmp_path)
    cmds = inst.install_cmds(ctx)
    assert cmds, "expected at least one install command"
    rendered = " ".join(c.rendered() for c in cmds)
    assert str(dest / "app") in rendered


def test_install_describe_renders_ref(tmp_project, tmp_path):
    from devops.targets.install import Install

    remote_dir = tmp_path / "rem"
    remote_dir.mkdir()
    _, enter = tmp_project
    with enter():
        inst = Install(
            name="x",
            artifact=DirectoryRef(str(remote_dir), target="app"),
            dest=tmp_path / "out",
        )
    desc = inst.describe()
    assert "file://" in desc
    assert "::app" in desc
