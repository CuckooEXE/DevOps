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
from devops.core.target import Artifact, DepKind, Target
from devops.remote import Ref
from devops.targets._paths import validate_relative_path
from devops.targets._specs import ResolvedSource, coerce_source, ref_prelude_for

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
        self._entries: dict[str, ResolvedSource] = {}
        for i, (archive_path, src) in enumerate(entries.items()):
            validate_relative_path(archive_path, "entries key", ident)
            resolved = coerce_source(
                src, kwarg=f"entries[{archive_path!r}]", ident=ident,
                project_root=self.project.root,
                deps=self.deps, dep_kind=DepKind.ARCHIVE,
                # Index suffix keeps multiple artifact-typed entries
                # with the same target.name distinguishable.
                dep_suffix=str(i),
            )
            # Best-effort config-time check for gzip — Target/Ref
            # sources resolve later, so the runner repeats this check
            # at build time.
            if (
                format == CompressionFormat.Gzip
                and resolved.path is not None
                and resolved.path.exists()
                and not resolved.path.is_file()
            ):
                kind = "directory" if resolved.path.is_dir() else "special"
                raise ValueError(
                    f"{ident}: Gzip source must be a regular file; "
                    f"got {resolved.path} ({kind})"
                )
            self._entries[archive_path] = resolved

    def output_path(self, ctx: "BuildContext") -> Path:
        base = self._archive_name or self.name
        return self.output_dir(ctx) / f"{base}{_EXT[self.format]}"

    def _config_inputs(self) -> list[Path]:
        """Walk path-typed sources at config time so edits to any contained
        file invalidate the cache. Target/Ref sources are tracked via
        their resolved ``output_path`` — the upstream's stamp covers
        internal change."""
        out: list[Path] = []
        for s in self._entries.values():
            if s.path is None:
                continue
            if s.path.is_dir():
                out.extend(sorted(p for p in s.path.rglob("*") if p.is_file()))
            else:
                out.append(s.path)
        return out

    def _artifact_paths(self, ctx: "BuildContext") -> list[Path]:
        ident = f"CompressedArtifact({self.name!r})"
        return [
            s.resolve(ctx, kwarg="entries", ident=ident)
            for s in self._entries.values()
            if s.path is None
        ]

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        ident = f"CompressedArtifact({self.name!r})"
        out_path = self.output_path(ctx)
        argv: list[str] = [
            sys.executable,
            "-m", "devops.targets._archive_runner",
            "--format", self.format.value,
            "--output", str(out_path),
        ]
        for archive_path, s in self._entries.items():
            src_path = s.resolve(ctx, kwarg="entries", ident=ident)
            argv.extend(["--entry", archive_path, str(src_path)])

        return [
            *ref_prelude_for(self._entries.values(), ctx),
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
        rows = [
            f"    {ap} <- {s.describe_str()}"
            for ap, s in self._entries.items()
        ]
        body = "\n".join(rows) if rows else "    (none)"
        return (
            f"CompressedArtifact {self.qualified_name} ({self.arch})\n"
            f"  format:  {self.format.value}\n"
            f"  entries:\n{body}"
        )
