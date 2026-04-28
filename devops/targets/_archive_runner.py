"""Build helper invoked by CompressedArtifact to write archives.

Usage::

    python -m devops.targets._archive_runner \\
        --format {gz|tar.gz|zip} \\
        --output <path> \\
        [--entry <archive_path> <src_path>]...

Lives outside the public surface (leading underscore) — only
CompressedArtifact's build_cmds calls it. Invoked via ``sys.executable``
so it runs in the same interpreter as devops itself, guaranteeing the
stdlib zip/tar/gzip modules are available without adding system tools
to the user's PATH.

Reproducibility: every entry's mtime/uid/gid/mode is normalized so two
builds with the same input bytes produce byte-identical archives. This
is a hard requirement of the build system — content-addressed downstream
steps would otherwise see a fresh hash on every rebuild.
"""

from __future__ import annotations

import argparse
import gzip
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

# Fixed timestamp baked into every archive entry. Zip's MS-DOS time can't
# represent year 0; use the DOS epoch (1980-01-01). Tar uses POSIX 0.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_NORM_MODE = 0o644
_NORM_DIR_MODE = 0o755


def _walk_files(src: Path) -> list[tuple[Path, Path]]:
    """Sorted (absolute, relative-to-src) pairs for every regular file under src.

    Sorted order makes archive layout deterministic across filesystems
    (os.listdir order varies). ``is_file()`` follows symlinks, so
    symlinks-to-files are included via their target content; broken
    symlinks and directories are skipped.
    """
    out: list[tuple[Path, Path]] = []
    for f in sorted(src.rglob("*")):
        if f.is_file():
            out.append((f, f.relative_to(src)))
    return out


def _norm_tarinfo(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    """Strip per-entry variability so archives are byte-reproducible."""
    ti.mtime = 0
    ti.uid = 0
    ti.gid = 0
    ti.uname = ""
    ti.gname = ""
    if ti.isdir():
        ti.mode = _NORM_DIR_MODE
    else:
        # Preserve the executable bit (matters for shipped binaries) but
        # canonicalize everything else to 0644 / 0755.
        ti.mode = _NORM_MODE | (0o111 if ti.mode & 0o100 else 0)
    return ti


def _add_to_tar(tar: tarfile.TarFile, src: Path, archive_path: str) -> None:
    if src.is_dir():
        for f, rel in _walk_files(src):
            tar.add(
                str(f),
                arcname=str(Path(archive_path) / rel),
                recursive=False,
                filter=_norm_tarinfo,
            )
    else:
        tar.add(
            str(src),
            arcname=archive_path,
            recursive=False,
            filter=_norm_tarinfo,
        )


def _zip_write_normalized(zf: zipfile.ZipFile, src: Path, arcname: str) -> None:
    """Write src as arcname with deterministic metadata."""
    mode = _NORM_MODE | (0o111 if src.stat().st_mode & 0o100 else 0)
    zi = zipfile.ZipInfo(filename=arcname, date_time=_ZIP_EPOCH)
    zi.compress_type = zipfile.ZIP_DEFLATED
    # Top 16 bits of external_attr hold the unix mode for a zipfile entry.
    zi.external_attr = (mode & 0xFFFF) << 16
    zf.writestr(zi, src.read_bytes())


def _add_to_zip(zf: zipfile.ZipFile, src: Path, archive_path: str) -> None:
    if src.is_dir():
        for f, rel in _walk_files(src):
            _zip_write_normalized(zf, f, str(Path(archive_path) / rel))
    else:
        _zip_write_normalized(zf, src, archive_path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="devops-archive-runner")
    p.add_argument("--format", required=True, choices=["gz", "tar.gz", "zip"])
    p.add_argument("--output", required=True)
    p.add_argument(
        "--entry",
        nargs=2,
        action="append",
        default=[],
        metavar=("ARCHIVE_PATH", "SRC_PATH"),
        help="map an input file/dir to an archive path; repeat per entry",
    )
    args = p.parse_args(argv)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    entries: list[tuple[str, Path]] = [
        (archive_path, Path(src)) for archive_path, src in args.entry
    ]

    if args.format == "gz":
        if len(entries) != 1:
            print("gz format requires exactly one entry", file=sys.stderr)
            return 2
        _, src = entries[0]
        if not src.is_file():
            print(
                f"gz format requires a regular file source; "
                f"got {src} ({'directory' if src.is_dir() else 'missing/special'})",
                file=sys.stderr,
            )
            return 2
        # Wrap an explicit GzipFile so we can pin mtime=0 — gzip.open's
        # default mtime is time.time(), which would differ on every run.
        with src.open("rb") as f_in, output.open("wb") as raw, \
             gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f_out:
            shutil.copyfileobj(f_in, f_out)
        return 0

    if args.format == "tar.gz":
        # tarfile.open(mode="w:gz") composes its own GzipFile internally
        # and never exposes mtime, so wrap a pinned GzipFile by hand and
        # write a plain tar into it.
        with output.open("wb") as raw, \
             gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz, \
             tarfile.open(fileobj=gz, mode="w") as tf:
            for archive_path, src in entries:
                _add_to_tar(tf, src, archive_path)
        return 0

    if args.format == "zip":
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            for archive_path, src in entries:
                _add_to_zip(zf, src, archive_path)
        return 0

    print(f"unknown format {args.format!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
