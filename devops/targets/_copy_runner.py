"""Build helper invoked by FileArtifact / DirectoryArtifact.

Replaces the shell ``mkdir -p / cp -p / cp -a / chmod / find -exec``
chains with a small Python script invoked under ``sys.executable``.
Same motivation as ``_archive_runner.py``: keeps system tools off the
required-tools list, sidesteps subtle BSD vs GNU differences in
``cp``/``find`` flag semantics, and turns a shell-form Command into
an argv-form Command (cheaper to render, cleaner in dry-run output,
verbatim cache key).

Two modes:

    --mode file --src <path> --dst <path> [--chmod 0755]
        Copy a single regular file, preserving source mtime.

    --mode dir  --src <path> --dst <path>
                [--file-mode 0644] [--dir-mode 0755]
        Recursively copy ``src/*`` into ``dst``. Wipes ``dst`` first
        so removed sources don't linger across rebuilds. Symlinks are
        preserved as symlinks; permissions / mtimes are preserved
        (Python's shutil.copytree(symlinks=True, copy_function=copy2)).
        Optional file_mode / dir_mode override per-entry mode after
        the copy.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def _copy_file(src: Path, dst: Path, mode: str | None) -> int:
    if not src.exists():
        print(f"copy file: src={src} does not exist", file=sys.stderr)
        return 2
    if not src.is_file():
        print(f"copy file: src={src} is not a regular file", file=sys.stderr)
        return 2
    dst.parent.mkdir(parents=True, exist_ok=True)
    # copy2 preserves mode + mtime + atime; matches the shell ``cp -p``
    # the previous implementation used.
    shutil.copy2(str(src), str(dst))
    if mode is not None:
        os.chmod(dst, int(mode, 8))
    return 0


def _copy_dir(
    src: Path,
    dst: Path,
    file_mode: str | None,
    dir_mode: str | None,
) -> int:
    if not src.exists():
        print(f"copy dir: src={src} does not exist", file=sys.stderr)
        return 2
    if not src.is_dir():
        print(f"copy dir: src={src} is not a directory", file=sys.stderr)
        return 2

    # Wipe dst so removed sources don't linger across rebuilds. Matches
    # the previous shell ``rm -rf {dst}`` semantic.
    if dst.exists() or dst.is_symlink():
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        else:
            shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    # symlinks=True preserves symlinks as symlinks (matches ``cp -a``).
    # copy_function=shutil.copy2 preserves mode + mtime per file.
    shutil.copytree(
        str(src), str(dst), symlinks=True, copy_function=shutil.copy2,
    )

    if file_mode is not None:
        fm = int(file_mode, 8)
        for f in dst.rglob("*"):
            if f.is_file() and not f.is_symlink():
                os.chmod(f, fm)
    if dir_mode is not None:
        dm = int(dir_mode, 8)
        # Walk top-down so we chmod the root after its parents finish
        # creating; here we chmod every dir under dst (and dst itself).
        os.chmod(dst, dm)
        for d in dst.rglob("*"):
            if d.is_dir() and not d.is_symlink():
                os.chmod(d, dm)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="devops-copy-runner")
    sub = p.add_subparsers(dest="mode", required=True)

    f = sub.add_parser("file", help="copy a single regular file")
    f.add_argument("--src", required=True)
    f.add_argument("--dst", required=True)
    f.add_argument("--chmod", default=None, help="octal mode applied after copy")

    d = sub.add_parser("dir", help="recursively copy a directory")
    d.add_argument("--src", required=True)
    d.add_argument("--dst", required=True)
    d.add_argument("--file-mode", dest="file_mode", default=None)
    d.add_argument("--dir-mode", dest="dir_mode", default=None)

    args = p.parse_args(argv)
    if args.mode == "file":
        return _copy_file(Path(args.src), Path(args.dst), args.chmod)
    if args.mode == "dir":
        return _copy_dir(
            Path(args.src), Path(args.dst), args.file_mode, args.dir_mode,
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
