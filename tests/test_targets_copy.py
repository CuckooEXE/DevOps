"""FileArtifact / DirectoryArtifact: validation, copy semantics, cache,
and Artifact-source flow."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from devops import cache
from devops.core import runner
from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.copy import DirectoryArtifact, FileArtifact
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


# --- FileArtifact: validation -------------------------------------------


def test_file_rejects_bad_src_type(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(TypeError, match="src"):
            FileArtifact(name="x", src=42)  # type: ignore[arg-type]


def test_file_rejects_absolute_dest(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="dest="):
            FileArtifact(name="x", src="a.txt", dest="/etc/foo.conf")


# --- FileArtifact: copy semantics ---------------------------------------


def test_file_copies_path_source(tmp_project, tmp_path):
    src = _write(tmp_path, "data/conf.toml", "key=1\n")
    _, enter = tmp_project
    with enter():
        fa = FileArtifact(name="conf", src="data/conf.toml")
    ctx = _ctx(tmp_path)
    runner.run_all(fa.build_cmds(ctx), use_cache=True)
    out = fa.output_path(ctx)
    assert out.is_file()
    assert out.read_text() == "key=1\n"
    assert out.name == "conf.toml"  # default dest = src basename
    del src


def test_file_uses_explicit_dest(tmp_project, tmp_path):
    _write(tmp_path, "src.txt", "hi")
    _, enter = tmp_project
    with enter():
        fa = FileArtifact(name="x", src="src.txt", dest="renamed.txt")
    ctx = _ctx(tmp_path)
    assert fa.output_path(ctx).name == "renamed.txt"


def test_file_applies_mode(tmp_project, tmp_path):
    _write(tmp_path, "exec.sh", "#!/bin/sh\n")
    _, enter = tmp_project
    with enter():
        fa = FileArtifact(name="exec", src="exec.sh", mode="0755")
    ctx = _ctx(tmp_path)
    runner.run_all(fa.build_cmds(ctx), use_cache=True)
    out = fa.output_path(ctx)
    assert out.is_file()
    assert (out.stat().st_mode & 0o777) == 0o755


def test_file_rejects_invalid_mode(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="mode="):
            FileArtifact(name="x", src="a.txt", mode="rwxr-xr-x")
        with pytest.raises(ValueError, match="mode="):
            FileArtifact(name="y", src="a.txt", mode="9999")


def test_file_rejects_empty_dest(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="dest=.*non-empty"):
            FileArtifact(name="x", src="a.txt", dest="")


def test_file_rejects_dotdot_dest(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match=r"\.\."):
            FileArtifact(name="x", src="a.txt", dest="../escape.txt")


def test_file_extra_inputs_flow_into_cache(tmp_project, tmp_path):
    _write(tmp_path, "src.txt", "a")
    conf = _write(tmp_path, "config.toml", "v=1")
    _, enter = tmp_project
    with enter():
        fa = FileArtifact(name="x", src="src.txt", extra_inputs=[conf])
    cmd = fa.build_cmds(_ctx(tmp_path))[0]
    assert conf.resolve() in cmd.inputs


# --- FileArtifact: Artifact source --------------------------------------


def test_file_artifact_source_flows_into_deps(tmp_project, tmp_path):
    _write(tmp_path, "msg.txt", "x")
    _, enter = tmp_project
    with enter():
        upstream = CustomArtifact(
            name="up",
            inputs={"msg": "msg.txt"},
            outputs=["up.out"],
            cmds=["cp {msg} {out[0]}"],
        )
        fa = FileArtifact(name="copy_up", src=upstream)
    assert upstream in fa.deps.values()


def test_file_artifact_source_resolves_at_build_time(tmp_project, tmp_path):
    _write(tmp_path, "msg.txt", "hello\n")
    _, enter = tmp_project
    with enter():
        upstream = CustomArtifact(
            name="up",
            inputs={"msg": "msg.txt"},
            outputs=["up.out"],
            cmds=["cp {msg} {out[0]}"],
        )
        fa = FileArtifact(name="copy_up", src=upstream)
    ctx = _ctx(tmp_path)
    runner.run_all(upstream.build_cmds(ctx), use_cache=True)
    runner.run_all(fa.build_cmds(ctx), use_cache=True)
    assert fa.output_path(ctx).read_text() == "hello\n"


# --- FileArtifact: cache ------------------------------------------------


def test_file_cache_invalidates_on_src_change(tmp_project, tmp_path):
    src = _write(tmp_path, "v.txt", "v1")
    _, enter = tmp_project
    with enter():
        fa = FileArtifact(name="v", src="v.txt")
    ctx = _ctx(tmp_path)
    cmd = fa.build_cmds(ctx)[0]
    runner.run(cmd, use_cache=True)
    assert cache.is_fresh(cmd)

    time.sleep(0.01)
    src.write_text("v2")
    os.utime(src, None)
    cmd2 = fa.build_cmds(ctx)[0]
    assert not cache.is_fresh(cmd2)


# --- DirectoryArtifact: validation --------------------------------------


def test_dir_rejects_missing_src(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(FileNotFoundError):
            DirectoryArtifact(name="x", src="nope")


def test_dir_rejects_file_src(tmp_project, tmp_path):
    _write(tmp_path, "file.txt", "")
    _, enter = tmp_project
    with enter():
        with pytest.raises(NotADirectoryError):
            DirectoryArtifact(name="x", src="file.txt")


def test_dir_rejects_absolute_dest(tmp_project, tmp_path):
    (tmp_path / "d").mkdir()
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="dest="):
            DirectoryArtifact(name="x", src="d", dest="/etc/foo")


# --- DirectoryArtifact: copy semantics ----------------------------------


def test_dir_copies_recursively(tmp_project, tmp_path):
    _write(tmp_path, "assets/icons/logo.png", "PNG")
    _write(tmp_path, "assets/strings.json", "{}")
    _, enter = tmp_project
    with enter():
        da = DirectoryArtifact(name="assets", src="assets")
    ctx = _ctx(tmp_path)
    runner.run_all(da.build_cmds(ctx), use_cache=True)
    out = da.output_path(ctx)
    assert (out / "icons" / "logo.png").read_text() == "PNG"
    assert (out / "strings.json").read_text() == "{}"


def test_dir_uses_explicit_dest(tmp_project, tmp_path):
    _write(tmp_path, "src/a.txt", "a")
    _, enter = tmp_project
    with enter():
        da = DirectoryArtifact(name="x", src="src", dest="renamed")
    ctx = _ctx(tmp_path)
    assert da.output_path(ctx).name == "renamed"


def test_dir_applies_modes(tmp_project, tmp_path):
    _write(tmp_path, "tree/a.txt", "a")
    _write(tmp_path, "tree/sub/b.txt", "b")
    _, enter = tmp_project
    with enter():
        da = DirectoryArtifact(
            name="t", src="tree", file_mode="0640", dir_mode="0750"
        )
    ctx = _ctx(tmp_path)
    runner.run_all(da.build_cmds(ctx), use_cache=True)
    out = da.output_path(ctx)
    for f in out.rglob("*"):
        if f.is_file():
            assert (f.stat().st_mode & 0o777) == 0o640, f
        elif f.is_dir():
            assert (f.stat().st_mode & 0o777) == 0o750, f


def test_dir_idempotent_clears_old_content(tmp_project, tmp_path):
    """Removed files in src don't linger in dst across rebuilds — and the
    cache must invalidate on its own (use_cache=True), since the dropped
    file changes the inputs tuple."""
    _write(tmp_path, "tree/a.txt", "a")
    _write(tmp_path, "tree/b.txt", "b")
    _, enter = tmp_project
    with enter():
        da = DirectoryArtifact(name="t", src="tree")
    ctx = _ctx(tmp_path)
    runner.run_all(da.build_cmds(ctx), use_cache=True)
    out = da.output_path(ctx)
    assert (out / "b.txt").exists()

    (tmp_path / "tree" / "b.txt").unlink()
    time.sleep(0.01)
    # Re-derive Command — _tracked_files is re-walked per call so the
    # second Command has a different inputs tuple.
    cmd = da.build_cmds(ctx)[0]
    assert not cache.is_fresh(cmd)
    runner.run_all([cmd], use_cache=True)
    assert (out / "a.txt").exists()
    assert not (out / "b.txt").exists()


def test_dir_rejects_invalid_modes(tmp_project, tmp_path):
    (tmp_path / "d").mkdir()
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="file_mode="):
            DirectoryArtifact(name="x", src="d", file_mode="bogus")
        with pytest.raises(ValueError, match="dir_mode="):
            DirectoryArtifact(name="y", src="d", dir_mode="9")


def test_dir_extra_inputs_flow_into_cache(tmp_project, tmp_path):
    _write(tmp_path, "tree/a.txt", "a")
    conf = _write(tmp_path, "config.toml", "v=1")
    _, enter = tmp_project
    with enter():
        da = DirectoryArtifact(name="x", src="tree", extra_inputs=[conf])
    cmd = da.build_cmds(_ctx(tmp_path))[0]
    assert conf.resolve() in cmd.inputs


# --- DirectoryArtifact: cache -------------------------------------------


def test_dir_cache_invalidates_on_inner_file_change(tmp_project, tmp_path):
    inner = _write(tmp_path, "tree/a.txt", "v1")
    _, enter = tmp_project
    with enter():
        da = DirectoryArtifact(name="t", src="tree")
    ctx = _ctx(tmp_path)
    cmd = da.build_cmds(ctx)[0]
    runner.run(cmd, use_cache=True)
    assert cache.is_fresh(cmd)

    time.sleep(0.01)
    inner.write_text("v2")
    os.utime(inner, None)
    assert not cache.is_fresh(cmd)
