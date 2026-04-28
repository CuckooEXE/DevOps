"""Incremental build cache via stamp files.

A Command is considered up-to-date when a stamp file next to its primary
output contains ``sha256(argv + input mtimes [+ depfile headers])``
matching the current value.

Header tracking: when a Command declares a `depfile=` (typically a clang
`-MMD -MF <path>` output), we parse it after the command has run and
fold every listed path's mtime into the hash on subsequent runs. This
catches ``#include`` graph changes even when the source file itself
didn't change — the same trick make/ninja/Bazel use.
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


def parse_depfile(depfile: Path) -> list[Path]:
    """Parse a Makefile-style ``<target>: <dep> <dep> ...`` file.

    Handles line continuations (``\\`` at end of line) and escaped spaces
    (``\\ ``). Ignores the target portion (everything up to the first
    unescaped ``:``). Returns deduplicated paths in source order.
    """
    try:
        raw = depfile.read_text()
    except (FileNotFoundError, OSError):
        return []

    # Join backslash-newline continuations
    text = raw.replace("\\\n", " ")
    # Strip the target: (and anything before it on the first non-empty line)
    if ":" in text:
        _, _, text = text.partition(":")

    # Tokenise; honour `\ ` as an escaped space inside a filename
    tokens: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text) and text[i + 1] == " ":
            current.append(" ")
            i += 2
            continue
        if c in " \t\r\n":
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(c)
        i += 1
    if current:
        tokens.append("".join(current))

    seen: set[Path] = set()
    out: list[Path] = []
    for tok in tokens:
        if not tok:
            continue
        p = Path(tok)
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _stat_contribution(h: "hashlib._Hash", p: Path) -> None:
    try:
        st = p.stat()
        h.update(f"|{p}|{st.st_mtime_ns}|{st.st_size}".encode())
    except FileNotFoundError:
        h.update(f"|{p}|missing".encode())


def _current_hash(cmd: Command) -> str:
    h = hashlib.sha256()
    h.update(shlex.join(cmd.argv).encode() if not cmd.shell else cmd.argv[0].encode())
    # Fold output paths into the hash so a stale stamp from a prior
    # argv-identical Command at a different output path can't masquerade
    # as fresh after the consumer renames its output.
    for o in cmd.outputs:
        h.update(f"|out:{o}".encode())
    for p in cmd.inputs:
        _stat_contribution(h, p)
    # Fold in discovered headers if the command emitted a depfile last run.
    if cmd.depfile is not None and cmd.depfile.is_file():
        for hdr in parse_depfile(cmd.depfile):
            _stat_contribution(h, hdr)
    return h.hexdigest()


def _output_present(o: Path) -> bool:
    """An output is "present" if it's a regular file that exists, or a
    non-empty directory. An empty directory doesn't count — a wiped
    PythonWheel output dir or a freshly-created CObjectFile obj/ would
    otherwise let ``is_fresh`` claim a freshness it can't deliver."""
    if o.is_file():
        return True
    if o.is_dir():
        return next(o.iterdir(), None) is not None
    return False


def is_fresh(cmd: Command) -> bool:
    stamp = _stamp_path(cmd)
    if stamp is None or not stamp.is_file():
        return False
    if not all(_output_present(o) for o in cmd.outputs):
        return False
    return stamp.read_text().strip() == _current_hash(cmd)


def write_stamp(cmd: Command) -> None:
    stamp = _stamp_path(cmd)
    if stamp is None:
        return
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(_current_hash(cmd))
