"""Incremental build cache via stamp files.

A Command is considered up-to-date when a stamp file next to its primary
output contains sha256(argv + input mtimes) matching the current value.
"""

from __future__ import annotations

import hashlib
import shlex
from pathlib import Path

from devops.core.command import Command


def _stamp_path(cmd: Command) -> Path | None:
    if not cmd.outputs:
        return None
    return cmd.outputs[0].with_suffix(cmd.outputs[0].suffix + ".stamp")


def _current_hash(cmd: Command) -> str:
    h = hashlib.sha256()
    h.update(shlex.join(cmd.argv).encode() if not cmd.shell else cmd.argv[0].encode())
    for p in cmd.inputs:
        try:
            st = p.stat()
            h.update(f"|{p}|{st.st_mtime_ns}|{st.st_size}".encode())
        except FileNotFoundError:
            h.update(f"|{p}|missing".encode())
    return h.hexdigest()


def is_fresh(cmd: Command) -> bool:
    stamp = _stamp_path(cmd)
    if stamp is None or not stamp.is_file():
        return False
    if not all(o.exists() for o in cmd.outputs):
        return False
    return stamp.read_text().strip() == _current_hash(cmd)


def write_stamp(cmd: Command) -> None:
    stamp = _stamp_path(cmd)
    if stamp is None:
        return
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(_current_hash(cmd))
