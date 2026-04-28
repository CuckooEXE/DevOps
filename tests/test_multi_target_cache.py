"""Multi-target end-to-end cache behaviour.

Single-Command cache freshness is covered by test_runner_cache.py;
this file covers the *integration* angle: a downstream artifact that
consumes an upstream's output should not rebuild when nothing
actually changed, and *should* rebuild when the upstream's output
content changes.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from devops import cache
from devops.context import BuildContext
from devops.core import runner
from devops.options import OptimizationLevel
from devops.targets.copy import FileArtifact
from devops.targets.custom import CustomArtifact


def _ctx(tmp: Path) -> BuildContext:
    return BuildContext(
        workspace_root=tmp,
        build_dir=tmp / "build",
        profile=OptimizationLevel.Debug,
    )


def _write(tmp: Path, rel: str, body: str) -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _stamp_for(cmd) -> Path:
    return cmd.outputs[0].with_suffix(cmd.outputs[0].suffix + ".stamp")


def test_two_target_chain_caches_steady_state(tmp_project, tmp_path):
    """A→B build twice with no input changes; B's stamp must not move."""
    _write(tmp_path, "src.txt", "v1\n")
    _, enter = tmp_project
    with enter():
        upstream = CustomArtifact(
            name="up",
            inputs={"src": "src.txt"},
            outputs=["up.out"],
            cmds=["cp {src} {out[0]}"],
        )
        downstream = FileArtifact(name="dn", src=upstream)
    ctx = _ctx(tmp_path)

    # First build — both run
    runner.run_all(upstream.build_cmds(ctx), use_cache=True)
    runner.run_all(downstream.build_cmds(ctx), use_cache=True)
    up_cmd = upstream.build_cmds(ctx)[0]
    dn_cmd = downstream.build_cmds(ctx)[0]
    up_stamp = _stamp_for(up_cmd)
    dn_stamp = _stamp_for(dn_cmd)
    up_mtime_1 = up_stamp.stat().st_mtime_ns
    dn_mtime_1 = dn_stamp.stat().st_mtime_ns

    # Both Commands report fresh
    assert cache.is_fresh(up_cmd)
    assert cache.is_fresh(dn_cmd)

    # Second build — nothing changed
    time.sleep(0.05)
    runner.run_all(upstream.build_cmds(ctx), use_cache=True)
    runner.run_all(downstream.build_cmds(ctx), use_cache=True)

    # Stamp files weren't rewritten — both steady-state
    assert up_stamp.stat().st_mtime_ns == up_mtime_1
    assert dn_stamp.stat().st_mtime_ns == dn_mtime_1


def test_upstream_change_invalidates_downstream(tmp_project, tmp_path):
    """An edit to the original source file must propagate through the
    upstream and reach the downstream FileArtifact's cache."""
    src = _write(tmp_path, "src.txt", "v1\n")
    _, enter = tmp_project
    with enter():
        upstream = CustomArtifact(
            name="up",
            inputs={"src": "src.txt"},
            outputs=["up.out"],
            cmds=["cp {src} {out[0]}"],
        )
        downstream = FileArtifact(name="dn", src=upstream)
    ctx = _ctx(tmp_path)
    runner.run_all(upstream.build_cmds(ctx), use_cache=True)
    runner.run_all(downstream.build_cmds(ctx), use_cache=True)
    out_path = downstream.output_path(ctx)
    assert out_path.read_text() == "v1\n"

    # Modify the original source. Upstream cache invalidates on the
    # input mtime; downstream cache invalidates because upstream's
    # output (its input) changes mtime when upstream rebuilds.
    time.sleep(0.05)
    src.write_text("v2\n")
    os.utime(src, None)

    assert not cache.is_fresh(upstream.build_cmds(ctx)[0])
    runner.run_all(upstream.build_cmds(ctx), use_cache=True)
    # Now the downstream's input mtime has changed — cache must miss.
    assert not cache.is_fresh(downstream.build_cmds(ctx)[0])
    runner.run_all(downstream.build_cmds(ctx), use_cache=True)
    assert out_path.read_text() == "v2\n"


def test_unchanged_dep_path_does_not_rebuild_downstream(tmp_project, tmp_path):
    """If the upstream rebuild produces byte-identical output (same
    content, same mtime preserved by cp -p), the downstream FileArtifact
    stays fresh on the second run."""
    _write(tmp_path, "src.txt", "stable\n")
    _, enter = tmp_project
    with enter():
        upstream = CustomArtifact(
            name="up",
            inputs={"src": "src.txt"},
            outputs=["up.out"],
            cmds=["cp -p {src} {out[0]}"],
        )
        downstream = FileArtifact(name="dn", src=upstream)
    ctx = _ctx(tmp_path)
    runner.run_all(upstream.build_cmds(ctx), use_cache=True)
    runner.run_all(downstream.build_cmds(ctx), use_cache=True)
    dn_stamp = _stamp_for(downstream.build_cmds(ctx)[0])
    dn_mtime = dn_stamp.stat().st_mtime_ns

    time.sleep(0.05)
    runner.run_all(upstream.build_cmds(ctx), use_cache=True)  # cache hit
    runner.run_all(downstream.build_cmds(ctx), use_cache=True)  # cache hit
    assert dn_stamp.stat().st_mtime_ns == dn_mtime
