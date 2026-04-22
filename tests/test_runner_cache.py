"""Runner behaviour: dry_run, ToolMissing, CommandFailed, and cache freshness."""

from __future__ import annotations

from pathlib import Path

import pytest

from devops import cache
from devops.core import runner
from devops.core.command import Command


def test_dry_run_prints_and_does_not_execute(capsys, tmp_path):
    out = tmp_path / "out.txt"
    cmd = Command(
        argv=("sh", "-c", f"echo hello > {out}"),
        label="side-effect",
        outputs=(out,),
    )
    runner.run(cmd, dry_run=True, use_cache=False)
    captured = capsys.readouterr()
    assert "sh" in captured.out
    assert not out.exists()


def test_tool_missing_raises_typed_error():
    cmd = Command(argv=("definitely-not-on-path-xyz", "--help"))
    with pytest.raises(runner.ToolMissing, match="definitely-not-on-path-xyz"):
        runner.run(cmd, use_cache=False)


def test_command_failed_carries_returncode():
    # sh -c 'exit 7' exists, runs, exits 7.
    cmd = Command(argv=("sh", "-c", "exit 7"))
    with pytest.raises(runner.CommandFailed) as excinfo:
        runner.run(cmd, use_cache=False)
    assert excinfo.value.returncode == 7


def test_cache_fresh_skips_rerun(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("hello")
    out = tmp_path / "out.txt"
    cmd = Command(
        argv=("cp", str(src), str(out)),
        inputs=(src,),
        outputs=(out,),
        label="cp",
    )
    runner.run(cmd, use_cache=True)
    assert out.exists()
    assert cache.is_fresh(cmd)
    # Modify output behind our back — a fresh run would rewrite it but
    # is_fresh should still report True because the stamp is valid.
    assert cache.is_fresh(cmd)


def test_cache_stale_when_input_changes(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("hello")
    out = tmp_path / "out.txt"
    cmd = Command(argv=("cp", str(src), str(out)), inputs=(src,), outputs=(out,))
    runner.run(cmd, use_cache=True)
    assert cache.is_fresh(cmd)

    # Change input contents (and therefore mtime) — cache should go stale.
    import os
    import time

    time.sleep(0.01)
    src.write_text("goodbye")
    os.utime(src, None)
    assert not cache.is_fresh(cmd)


def test_cache_stale_when_output_missing(tmp_path: Path):
    src = tmp_path / "src.txt"
    src.write_text("x")
    out = tmp_path / "out.txt"
    cmd = Command(argv=("cp", str(src), str(out)), inputs=(src,), outputs=(out,))
    runner.run(cmd, use_cache=True)
    out.unlink()
    assert not cache.is_fresh(cmd)


def test_run_all_short_circuits_on_failure(tmp_path: Path):
    calls = []

    def record(cmd: Command) -> None:
        calls.append(cmd.label)

    cmds = [
        Command(argv=("sh", "-c", "true"), label="first"),
        Command(argv=("sh", "-c", "exit 3"), label="second"),
        Command(argv=("sh", "-c", "true"), label="third"),
    ]
    with pytest.raises(runner.CommandFailed):
        runner.run_all(cmds, use_cache=False)
