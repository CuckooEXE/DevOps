"""CompressedArtifact — bundle files / directories / Targets into one archive.

The mapping interface (``entries=``) places arbitrary inputs at arbitrary
archive paths::

    CompressedArtifact(
        name="release",
        format=CompressionFormat.TarGzip,
        entries={
            "bin/myapp":         app_binary,         # an Artifact
            "config/app.conf":   "etc/app.conf",     # a file path
            "data":              "shared/data",      # a directory path
            "include":           headers_target,     # a HeadersOnly Artifact
        },
    )

For TarGzip and Zip, sources land at their archive path inside the
archive. Directory sources contribute their files under the archive
path prefix; file sources land at exactly that path.

For Gzip (single-file gzip), ``entries`` must contain exactly one entry
and the archive path is ignored — gzip wraps a single file with no
internal layout.

Implementation: archives are written by Python's stdlib zipfile/tarfile/
gzip via a small helper module (``devops.targets._archive_runner``)
invoked under ``sys.executable``. That keeps system tools off the
required-tools list and gives deterministic file ordering inside the
archive.
"""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from devops.core.command import Command
from devops.core.target import Artifact, Target
from devops.remote import Ref
from devops.targets._paths import validate_relative_path
from devops.targets._specs import inline_ref_build_cmds, resolve_target_spec

if TYPE_CHECKING:
    from devops.context import BuildContext


class CompressionFormat(Enum):
    Gzip = "gz"
    TarGzip = "tar.gz"
    Zip = "zip"


_EXT = {
    CompressionFormat.Gzip: ".gz",
    CompressionFormat.TarGzip: ".tar.gz",
    CompressionFormat.Zip: ".zip",
}


class CompressedArtifact(Artifact):
    """Compress files / directories / Targets into a single archive.

    Args:
        name:           target name
        format:         CompressionFormat enum (Gzip, TarGzip, or Zip)
        entries:        ``{archive_path: source}`` mapping. Source can be
                        a str/Path file, a str/Path directory, an
                        Artifact, or a Ref (resolved to a remote-project
                        Artifact at build time). Artifact sources flow
                        into deps so the archive rebuilds when any
                        source changes.
        archive_name:   filename stem for the produced archive. Defaults
                        to ``name``. The format extension is appended
                        automatically (".tar.gz", ".zip", ".gz").
    """

    def __init__(
        self,
        name: str,
        format: CompressionFormat,
        entries: "dict[str, str | Path | Artifact | Ref]",
        archive_name: str | None = None,
        deps: dict[str, Target] | None = None,
        version: str | None = None,
        doc: str | None = None,
        arch: str = "host",
        extra_inputs: "tuple[str | Path, ...] | list[str | Path] | None" = None,
        required_tools: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        ident = f"CompressedArtifact({name!r})"
        if not isinstance(format, CompressionFormat):
            raise TypeError(
                f"{ident}: format= must be CompressionFormat, "
                f"got {type(format).__name__}"
            )
        if not entries:
            raise ValueError(f"{ident}: entries= must not be empty")
        if format == CompressionFormat.Gzip and len(entries) != 1:
            raise ValueError(
                f"{ident}: Gzip wraps a single file; "
                f"entries= must contain exactly one entry, got {len(entries)}"
            )

        super().__init__(
            name=name, deps=deps, version=version, doc=doc, arch=arch,
            extra_inputs=extra_inputs, required_tools=required_tools,
        )
        self.format = format
        self._archive_name = archive_name
        # Each entry stored as Path (literal fs source) or Target/Ref
        # (resolved at build_cmds time).
        self._entries: dict[str, "Path | Target | Ref"] = {}
        for i, (archive_path, src) in enumerate(entries.items()):
            validate_relative_path(archive_path, "entries key", ident)
            if isinstance(src, Artifact):
                self._entries[archive_path] = src
                self.deps[f"_arc_{i}"] = src
            elif isinstance(src, Ref):
                self._entries[archive_path] = src
            elif isinstance(src, (str, Path)):
                p = Path(src)
                if not p.is_absolute():
                    p = (self.project.root / p).resolve()
                # Best-effort config-time check for gzip — Target/Ref
                # sources resolve later, so the runner repeats this
                # check at build time.
                if (
                    format == CompressionFormat.Gzip
                    and p.exists()
                    and not p.is_file()
                ):
                    kind = "directory" if p.is_dir() else "special"
                    raise ValueError(
                        f"{ident}: Gzip source must be a regular file; "
                        f"got {p} ({kind})"
                    )
                self._entries[archive_path] = p
            else:
                raise TypeError(
                    f"{ident} entry {archive_path!r}: source must be "
                    f"str, Path, Artifact, or Ref; "
                    f"got {type(src).__name__}"
                )

    def output_path(self, ctx: "BuildContext") -> Path:
        base = self._archive_name or self.name
        return self.output_dir(ctx) / f"{base}{_EXT[self.format]}"

    def _resolve_entry(
        self, src: "Path | Target | Ref", ctx: "BuildContext"
    ) -> Path:
        if isinstance(src, Path):
            return src
        target = resolve_target_spec(
            src,
            kwarg="entries", ident=f"CompressedArtifact({self.name!r})",
        )
        if not isinstance(target, Artifact):
            raise TypeError(
                f"CompressedArtifact({self.name!r}): entry resolved to "
                f"{type(target).__name__}, expected an Artifact"
            )
        return target.output_path(ctx)

    def _config_inputs(self) -> list[Path]:
        """Walk path-typed sources at config time so edits to any contained
        file invalidate the cache. Target/Ref sources are tracked via
        their resolved ``output_path`` — the upstream's stamp covers
        internal change."""
        out: list[Path] = []
        for src in self._entries.values():
            if isinstance(src, Path):
                if src.is_dir():
                    out.extend(sorted(p for p in src.rglob("*") if p.is_file()))
                else:
                    out.append(src)
        return out

    def _artifact_paths(self, ctx: "BuildContext") -> list[Path]:
        out: list[Path] = []
        for src in self._entries.values():
            if isinstance(src, Path):
                continue
            out.append(self._resolve_entry(src, ctx))
        return out

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        out_path = self.output_path(ctx)
        prelude = inline_ref_build_cmds(
            [src for src in self._entries.values() if isinstance(src, Ref)],
            ctx,
        )
        argv: list[str] = [
            sys.executable,
            "-m", "devops.targets._archive_runner",
            "--format", self.format.value,
            "--output", str(out_path),
        ]
        for archive_path, src in self._entries.items():
            src_path = self._resolve_entry(src, ctx)
            argv.extend(["--entry", archive_path, str(src_path)])

        return [
            *prelude,
            Command(
                argv=tuple(argv),
                cwd=self.project.root,
                label=f"{self.format.value} {self.name}",
                inputs=(*self._config_inputs(), *self._artifact_paths(ctx),
                        *self._extra_inputs),
                outputs=(out_path,),
            ),
        ]

    def describe(self) -> str:
        rows = []
        for ap, src in self._entries.items():
            if isinstance(src, Artifact):
                rows.append(f"    {ap} <- {src.qualified_name}")
            else:
                rows.append(f"    {ap} <- {src}")
        body = "\n".join(rows) if rows else "    (none)"
        return (
            f"CompressedArtifact {self.qualified_name} ({self.arch})\n"
            f"  format:  {self.format.value}\n"
            f"  entries:\n{body}"
        )
