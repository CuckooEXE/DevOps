"""CompressedArtifact: validation, archive layout, format-specific
output, mixed Path/Artifact entries, cache invalidation."""

from __future__ import annotations

import gzip
import os
import sys
import tarfile
import time
import zipfile
from pathlib import Path

import pytest

from devops import cache
from devops.core import runner
from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.archive import CompressedArtifact, CompressionFormat
from devops.targets.custom import CustomArtifact


def _ctx(tmp: Path) -> BuildContext:
    return BuildContext(
        workspace_root=tmp,
        build_dir=tmp / "build",
        profile=OptimizationLevel.Debug,
    )


def _write(tmp: Path, rel: str, body: str = "") -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


# --- validation ---------------------------------------------------------


def test_rejects_non_enum_format(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(TypeError, match="format="):
            CompressedArtifact(
                name="x", format="zip", entries={"a": "a.txt"},  # type: ignore[arg-type]
            )


def test_rejects_empty_entries(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="entries="):
            CompressedArtifact(
                name="x", format=CompressionFormat.TarGzip, entries={},
            )


def test_gzip_requires_single_entry(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="Gzip"):
            CompressedArtifact(
                name="x",
                format=CompressionFormat.Gzip,
                entries={"a": "a.txt", "b": "b.txt"},
            )


def test_rejects_absolute_archive_path(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="relative"):
            CompressedArtifact(
                name="x",
                format=CompressionFormat.TarGzip,
                entries={"/etc/foo": "src.txt"},
            )


def test_rejects_dotdot_archive_path(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match=r"\.\."):
            CompressedArtifact(
                name="x",
                format=CompressionFormat.TarGzip,
                entries={"../oops": "src.txt"},
            )


def test_rejects_bad_entry_source_type(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(TypeError, match="source"):
            CompressedArtifact(
                name="x",
                format=CompressionFormat.TarGzip,
                entries={"a": 42},  # type: ignore[dict-item]
            )


def test_gzip_rejects_directory_source(tmp_project, tmp_path):
    (tmp_path / "data").mkdir()
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="Gzip source must be a regular file"):
            CompressedArtifact(
                name="x",
                format=CompressionFormat.Gzip,
                entries={"_": "data"},
            )


def test_archive_extra_inputs_flow_into_cache(tmp_project, tmp_path):
    _write(tmp_path, "a.txt", "a")
    conf = _write(tmp_path, "config.toml", "v=1")
    _, enter = tmp_project
    with enter():
        ca = CompressedArtifact(
            name="x",
            format=CompressionFormat.TarGzip,
            entries={"a.txt": "a.txt"},
            extra_inputs=[conf],
        )
    cmd = ca.build_cmds(_ctx(tmp_path))[0]
    assert conf.resolve() in cmd.inputs


# --- output paths -------------------------------------------------------


def test_output_path_uses_format_extension(tmp_project, tmp_path):
    _write(tmp_path, "a.txt", "a")
    _, enter = tmp_project
    with enter():
        gz = CompressedArtifact(
            name="a", format=CompressionFormat.Gzip, entries={"_": "a.txt"},
        )
        tgz = CompressedArtifact(
            name="b", format=CompressionFormat.TarGzip, entries={"a.txt": "a.txt"},
        )
        z = CompressedArtifact(
            name="c", format=CompressionFormat.Zip, entries={"a.txt": "a.txt"},
        )
    ctx = _ctx(tmp_path)
    assert gz.output_path(ctx).name == "a.gz"
    assert tgz.output_path(ctx).name == "b.tar.gz"
    assert z.output_path(ctx).name == "c.zip"


def test_archive_name_overrides_stem(tmp_project, tmp_path):
    _write(tmp_path, "a.txt", "a")
    _, enter = tmp_project
    with enter():
        ca = CompressedArtifact(
            name="x",
            format=CompressionFormat.TarGzip,
            entries={"a.txt": "a.txt"},
            archive_name="release-1.0",
        )
    assert ca.output_path(_ctx(tmp_path)).name == "release-1.0.tar.gz"


# --- topo-sort wiring ---------------------------------------------------


def test_artifact_entries_flow_into_deps(tmp_project, tmp_path):
    _write(tmp_path, "msg.txt", "x")
    _, enter = tmp_project
    with enter():
        upstream = CustomArtifact(
            name="up",
            inputs={"msg": "msg.txt"},
            outputs=["up.out"],
            cmds=["cp {msg} {out[0]}"],
        )
        ca = CompressedArtifact(
            name="bundle",
            format=CompressionFormat.TarGzip,
            entries={"bin/up": upstream},
        )
    assert upstream in ca.deps.values()


# --- command shape ------------------------------------------------------


def test_build_cmd_uses_python_helper(tmp_project, tmp_path):
    _write(tmp_path, "a.txt", "a")
    _, enter = tmp_project
    with enter():
        ca = CompressedArtifact(
            name="x",
            format=CompressionFormat.TarGzip,
            entries={"a.txt": "a.txt"},
        )
    cmd = ca.build_cmds(_ctx(tmp_path))[0]
    assert not cmd.shell
    assert cmd.argv[0] == sys.executable
    assert cmd.argv[1:4] == ("-m", "devops.targets._archive_runner", "--format")


# --- end-to-end archives ------------------------------------------------


def test_gzip_produces_valid_archive(tmp_project, tmp_path):
    _write(tmp_path, "msg.txt", "hello world\n")
    _, enter = tmp_project
    with enter():
        ca = CompressedArtifact(
            name="msg",
            format=CompressionFormat.Gzip,
            entries={"_": "msg.txt"},
        )
    ctx = _ctx(tmp_path)
    runner.run_all(ca.build_cmds(ctx), use_cache=True)
    out = ca.output_path(ctx)
    assert out.is_file()
    with gzip.open(out, "rt") as f:
        assert f.read() == "hello world\n"


def test_targz_layout_matches_entries(tmp_project, tmp_path):
    _write(tmp_path, "etc/app.conf", "verbose=1\n")
    _write(tmp_path, "data/a.txt", "A")
    _write(tmp_path, "data/sub/b.txt", "B")
    _, enter = tmp_project
    with enter():
        ca = CompressedArtifact(
            name="release",
            format=CompressionFormat.TarGzip,
            entries={
                "config/app.conf": "etc/app.conf",
                "share/data": "data",  # dir source -> contents under prefix
            },
        )
    ctx = _ctx(tmp_path)
    runner.run_all(ca.build_cmds(ctx), use_cache=True)
    out = ca.output_path(ctx)
    assert out.is_file()
    with tarfile.open(out, "r:gz") as tf:
        names = set(tf.getnames())
    assert "config/app.conf" in names
    assert "share/data/a.txt" in names
    assert "share/data/sub/b.txt" in names


def test_zip_layout_matches_entries(tmp_project, tmp_path):
    _write(tmp_path, "a.txt", "A")
    _write(tmp_path, "tree/b.txt", "B")
    _, enter = tmp_project
    with enter():
        ca = CompressedArtifact(
            name="bundle",
            format=CompressionFormat.Zip,
            entries={
                "top.txt": "a.txt",
                "nested": "tree",
            },
        )
    ctx = _ctx(tmp_path)
    runner.run_all(ca.build_cmds(ctx), use_cache=True)
    out = ca.output_path(ctx)
    assert out.is_file()
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "top.txt" in names
    assert "nested/b.txt" in names


def test_targz_with_artifact_source(tmp_project, tmp_path):
    _write(tmp_path, "msg.txt", "from upstream\n")
    _, enter = tmp_project
    with enter():
        upstream = CustomArtifact(
            name="up",
            inputs={"msg": "msg.txt"},
            outputs=["up.out"],
            cmds=["cp {msg} {out[0]}"],
        )
        ca = CompressedArtifact(
            name="bundle",
            format=CompressionFormat.TarGzip,
            entries={"bin/up": upstream},
        )
    ctx = _ctx(tmp_path)
    runner.run_all(upstream.build_cmds(ctx), use_cache=True)
    runner.run_all(ca.build_cmds(ctx), use_cache=True)
    out = ca.output_path(ctx)
    with tarfile.open(out, "r:gz") as tf:
        names = set(tf.getnames())
    assert "bin/up" in names


def test_targz_drops_removed_entries_on_rebuild(tmp_project, tmp_path):
    """A reconfigured artifact with fewer entries produces a clean archive."""
    _write(tmp_path, "a.txt", "a")
    _write(tmp_path, "b.txt", "b")
    _, enter = tmp_project
    with enter():
        ca = CompressedArtifact(
            name="r",
            format=CompressionFormat.TarGzip,
            entries={"a.txt": "a.txt", "b.txt": "b.txt"},
        )
    ctx = _ctx(tmp_path)
    runner.run_all(ca.build_cmds(ctx), use_cache=False)
    out = ca.output_path(ctx)
    with tarfile.open(out, "r:gz") as tf:
        names1 = set(tf.getnames())
    assert "a.txt" in names1 and "b.txt" in names1

    from devops import registry
    proj = ca.project
    registry.reset()
    del ca

    with registry.active_project(proj):
        ca2 = CompressedArtifact(
            name="r",
            format=CompressionFormat.TarGzip,
            entries={"a.txt": "a.txt"},
        )
    runner.run_all(ca2.build_cmds(ctx), use_cache=False)
    with tarfile.open(out, "r:gz") as tf:
        names2 = set(tf.getnames())
    assert names2 == {"a.txt"}


# --- cache --------------------------------------------------------------


def test_targz_cache_invalidates_on_dir_file_change(tmp_project, tmp_path):
    inner = _write(tmp_path, "tree/x.txt", "v1")
    _, enter = tmp_project
    with enter():
        ca = CompressedArtifact(
            name="r",
            format=CompressionFormat.TarGzip,
            entries={"share": "tree"},
        )
    ctx = _ctx(tmp_path)
    cmd = ca.build_cmds(ctx)[0]
    runner.run(cmd, use_cache=True)
    assert cache.is_fresh(cmd)

    time.sleep(0.01)
    inner.write_text("v2")
    os.utime(inner, None)
    assert not cache.is_fresh(cmd)


# --- reproducibility ----------------------------------------------------


def _ctx_at(tmp: Path, build_subdir: str) -> BuildContext:
    return BuildContext(
        workspace_root=tmp,
        build_dir=tmp / build_subdir,
        profile=OptimizationLevel.Debug,
    )


def _build_twice(ca: CompressedArtifact, tmp_path: Path, src_to_touch: Path) -> tuple[bytes, bytes]:
    ctx1 = _ctx_at(tmp_path, "b1")
    runner.run_all(ca.build_cmds(ctx1), use_cache=False)
    b1 = ca.output_path(ctx1).read_bytes()

    # Perturb source mtime — a non-reproducible runner would leak this
    # into the archive header and produce different bytes.
    time.sleep(0.05)
    os.utime(src_to_touch, None)

    ctx2 = _ctx_at(tmp_path, "b2")
    runner.run_all(ca.build_cmds(ctx2), use_cache=False)
    b2 = ca.output_path(ctx2).read_bytes()
    return b1, b2


def test_gzip_byte_identical_across_runs(tmp_project, tmp_path):
    src = _write(tmp_path, "msg.txt", "hello\n")
    _, enter = tmp_project
    with enter():
        ca = CompressedArtifact(
            name="g", format=CompressionFormat.Gzip, entries={"_": "msg.txt"},
        )
    b1, b2 = _build_twice(ca, tmp_path, src)
    assert b1 == b2


def test_targz_byte_identical_across_runs(tmp_project, tmp_path):
    a = _write(tmp_path, "data/a.txt", "alpha\n")
    _write(tmp_path, "data/b.txt", "beta\n")
    _, enter = tmp_project
    with enter():
        ca = CompressedArtifact(
            name="r",
            format=CompressionFormat.TarGzip,
            entries={"data": "data"},
        )
    b1, b2 = _build_twice(ca, tmp_path, a)
    assert b1 == b2


def test_zip_byte_identical_across_runs(tmp_project, tmp_path):
    a = _write(tmp_path, "a.txt", "alpha\n")
    _write(tmp_path, "tree/b.txt", "beta\n")
    _, enter = tmp_project
    with enter():
        ca = CompressedArtifact(
            name="z",
            format=CompressionFormat.Zip,
            entries={"top.txt": "a.txt", "nested": "tree"},
        )
    b1, b2 = _build_twice(ca, tmp_path, a)
    assert b1 == b2


def test_targz_entry_metadata_normalized(tmp_project, tmp_path):
    """Per-entry mtime/uid/gid in the tar header are zeroed."""
    _write(tmp_path, "data/a.txt", "alpha\n")
    _, enter = tmp_project
    with enter():
        ca = CompressedArtifact(
            name="r",
            format=CompressionFormat.TarGzip,
            entries={"data": "data"},
        )
    ctx = _ctx(tmp_path)
    runner.run_all(ca.build_cmds(ctx), use_cache=False)
    with tarfile.open(ca.output_path(ctx), "r:gz") as tf:
        for ti in tf.getmembers():
            assert ti.mtime == 0, f"{ti.name} mtime={ti.mtime}"
            assert ti.uid == 0
            assert ti.gid == 0
            assert ti.uname == ""
            assert ti.gname == ""


def test_zip_entry_metadata_normalized(tmp_project, tmp_path):
    """Per-entry date_time in the zip header is the DOS epoch."""
    _write(tmp_path, "a.txt", "alpha\n")
    _, enter = tmp_project
    with enter():
        ca = CompressedArtifact(
            name="z",
            format=CompressionFormat.Zip,
            entries={"a.txt": "a.txt"},
        )
    ctx = _ctx(tmp_path)
    runner.run_all(ca.build_cmds(ctx), use_cache=False)
    with zipfile.ZipFile(ca.output_path(ctx)) as zf:
        for zi in zf.infolist():
            assert zi.date_time == (1980, 1, 1, 0, 0, 0)
