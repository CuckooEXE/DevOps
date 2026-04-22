"""Execute Commands; handles dry-run, missing tools, output streaming."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from devops import cache
from devops.core.command import Command


class ToolMissing(RuntimeError):
    pass


class CommandFailed(RuntimeError):
    def __init__(self, cmd: Command, returncode: int):
        super().__init__(f"[exit {returncode}] {cmd.rendered()}")
        self.cmd = cmd
        self.returncode = returncode


def _ensure_output_parents(cmd: Command) -> None:
    for o in cmd.outputs:
        o.parent.mkdir(parents=True, exist_ok=True)


def _first_arg_available(cmd: Command) -> bool:
    if cmd.shell:
        return True  # shell decides
    exe = cmd.argv[0]
    return shutil.which(exe) is not None or Path(exe).is_file()


def run(cmd: Command, *, verbose: bool = False, dry_run: bool = False, use_cache: bool = True) -> None:
    if use_cache and cache.is_fresh(cmd):
        if verbose:
            print(f"[cached] {cmd.label or cmd.rendered()}", file=sys.stderr)
        return

    if dry_run:
        print(cmd.rendered())
        return

    if not _first_arg_available(cmd):
        raise ToolMissing(f"required tool not on PATH: {cmd.argv[0]}")

    _ensure_output_parents(cmd)
    env = os.environ.copy()
    env.update(cmd.env)
    if verbose:
        print(f"$ {cmd.rendered()}", file=sys.stderr)

    if cmd.shell:
        result = subprocess.run(cmd.argv[0], shell=True, cwd=cmd.cwd, env=env)
    else:
        result = subprocess.run(list(cmd.argv), cwd=cmd.cwd, env=env)

    if result.returncode != 0:
        raise CommandFailed(cmd, result.returncode)

    if use_cache:
        cache.write_stamp(cmd)


def run_all(cmds: list[Command], **kwargs: object) -> None:
    for c in cmds:
        run(c, **kwargs)  # type: ignore[arg-type]
