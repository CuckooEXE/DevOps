"""FileArtifact, DirectoryArtifact — verbatim copies into the build tree.

Reach for these when something the build needs to ship — a config file,
an asset directory, a static manifest — has to land under build/
alongside compiled artifacts. Both accept either a filesystem path or
another Artifact whose output_path resolves at build time.

For arbitrary transformations, use CustomArtifact instead.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from devops.core.command import Command
from devops.core.target import Artifact, DepKind, Target
from devops.remote import Ref
from devops.targets._paths import validate_octal_mode, validate_relative_path
from devops.targets._specs import inline_ref_build_cmds, resolve_target_spec

if TYPE_CHECKING:
    from devops.context import BuildContext


class FileArtifact(Artifact):
    """Copy a single file into the build output directory.

    Args:
        name:    target name
        src:     source — a file path (str/Path) resolved against the
                 project root if relative, an Artifact, or a Ref
                 (resolves to a remote-project Artifact at build time)
        dest:    output filename relative to ``output_dir``. Defaults to
                 the source's basename.
        mode:    octal chmod string applied after copy (e.g. "0755").
                 When omitted the source's mode is preserved.
    """

    def __init__(
        self,
        name: str,
        src: "str | Path | Artifact | Ref",
        dest: str | None = None,
        mode: str | None = None,
        deps: dict[str, Target] | None = None,
        version: str | None = None,
        doc: str | None = None,
        arch: str = "host",
        extra_inputs: "tuple[str | Path, ...] | list[str | Path] | None" = None,
        required_tools: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        super().__init__(
            name=name, deps=deps, version=version, doc=doc, arch=arch,
            extra_inputs=extra_inputs, required_tools=required_tools,
        )
        ident = f"FileArtifact({name!r})"
        # Stored shapes:
        #   _src_path: Path           — for raw filesystem srcs
        #   _src_spec: Target | Ref   — resolved at build time
        self._src_path: Path | None = None
        self._src_spec: "Target | Ref | None" = None
        if isinstance(src, Artifact):
            self._src_spec = src
            self.register_dep(DepKind.COPY, src)
        elif isinstance(src, Ref):
            self._src_spec = src  # remote — resolved at build time
        elif isinstance(src, (str, Path)):
            p = Path(src)
            if not p.is_absolute():
                p = (self.project.root / p).resolve()
            self._src_path = p
        else:
            raise TypeError(
                f"{ident}: src must be str, Path, Artifact, or Ref; "
                f"got {type(src).__name__}"
            )
        if dest is not None:
            validate_relative_path(dest, "dest", ident)
        validate_octal_mode(mode, "mode", ident)
        self._dest = dest
        self._mode = mode

    def _resolve_src(self, ctx: "BuildContext") -> Path:
        if self._src_spec is not None:
            target = resolve_target_spec(
                self._src_spec,
                kwarg="src", ident=f"FileArtifact({self.name!r})",
            )
            if not isinstance(target, Artifact):
                raise TypeError(
                    f"FileArtifact({self.name!r}): src resolved to "
                    f"{type(target).__name__}, expected an Artifact"
                )
            return target.output_path(ctx)
        assert self._src_path is not None
        return self._src_path

    def output_path(self, ctx: "BuildContext") -> Path:
        dest_name = self._dest or self._resolve_src(ctx).name
        return self.output_dir(ctx) / dest_name

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        src = self._resolve_src(ctx)
        dst = self.output_path(ctx)
        sq_src = shlex.quote(str(src))
        sq_dst = shlex.quote(str(dst))
        sq_parent = shlex.quote(str(dst.parent))
        line = f"mkdir -p {sq_parent} && cp -p {sq_src} {sq_dst}"
        if self._mode is not None:
            line += f" && chmod {self._mode} {sq_dst}"
        prelude = inline_ref_build_cmds(
            [self._src_spec] if isinstance(self._src_spec, Ref) else [],
            ctx,
        )
        return [
            *prelude,
            Command.shell_cmd(
                line,
                cwd=self.project.root,
                label=f"copy {src.name} -> {self.name}",
                inputs=(src, *self._extra_inputs),
                outputs=(dst,),
            ),
        ]

    def describe(self) -> str:
        if self._src_path is not None:
            src_str = str(self._src_path)
        elif isinstance(self._src_spec, Target):
            src_str = self._src_spec.qualified_name
        elif isinstance(self._src_spec, Ref):
            src_str = self._src_spec.to_spec()
        else:
            src_str = "?"
        return (
            f"FileArtifact {self.qualified_name} ({self.arch})\n"
            f"  src:  {src_str}\n"
            f"  dest: {self._dest or '(basename of src)'}"
        )


class DirectoryArtifact(Artifact):
    """Recursively copy a directory into the build output directory.

    Always uses ``cp -a`` (preserves mode, timestamps, symlinks). If you
    want to override modes, use ``file_mode`` / ``dir_mode``.

    Args:
        name:        target name
        src:         source — a directory path (str/Path) resolved
                     against the project root if relative, an Artifact
                     whose ``output_path`` is a directory (e.g.
                     ``HeadersOnly``), or a Ref (resolves to a
                     remote-project Artifact at build time)
        dest:        output subdirectory name. Defaults to ``name``.
        file_mode:   octal mode applied to every regular file after copy
                     (e.g. "0644"). None to preserve the source mode.
        dir_mode:    octal mode applied to every directory after copy.
                     None to preserve the source mode.

    Cache caveat: the contents of a *path* ``src`` are walked once per
    ``devops`` invocation. Files added to ``src`` between invocations
    *are* picked up; files added during a single ``watch`` session
    (which reuses the same configured graph) are not. For Target/Ref
    sources the upstream Target's stamp covers internal change.
    """

    def __init__(
        self,
        name: str,
        src: "str | Path | Artifact | Ref",
        dest: str | None = None,
        file_mode: str | None = None,
        dir_mode: str | None = None,
        deps: dict[str, Target] | None = None,
        version: str | None = None,
        doc: str | None = None,
        arch: str = "host",
        extra_inputs: "tuple[str | Path, ...] | list[str | Path] | None" = None,
        required_tools: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        super().__init__(
            name=name, deps=deps, version=version, doc=doc, arch=arch,
            extra_inputs=extra_inputs, required_tools=required_tools,
        )
        ident = f"DirectoryArtifact({name!r})"
        self._src_path: Path | None = None
        self._src_spec: "Target | Ref | None" = None
        if isinstance(src, Artifact):
            self._src_spec = src
            self.register_dep(DepKind.COPY, src)
        elif isinstance(src, Ref):
            self._src_spec = src
        elif isinstance(src, (str, Path)):
            p = Path(src)
            if not p.is_absolute():
                p = (self.project.root / p).resolve()
            if not p.exists():
                raise FileNotFoundError(f"{ident}: src={p} does not exist")
            if not p.is_dir():
                raise NotADirectoryError(f"{ident}: src={p} is not a directory")
            self._src_path = p
        else:
            raise TypeError(
                f"{ident}: src must be str, Path, Artifact, or Ref; "
                f"got {type(src).__name__}"
            )
        if dest is not None:
            validate_relative_path(dest, "dest", ident)
        validate_octal_mode(file_mode, "file_mode", ident)
        validate_octal_mode(dir_mode, "dir_mode", ident)
        self._dest = dest
        self._file_mode = file_mode
        self._dir_mode = dir_mode

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / (self._dest or self.name)

    def _resolve_src(self, ctx: "BuildContext") -> Path:
        if self._src_spec is not None:
            target = resolve_target_spec(
                self._src_spec,
                kwarg="src", ident=f"DirectoryArtifact({self.name!r})",
            )
            if not isinstance(target, Artifact):
                raise TypeError(
                    f"DirectoryArtifact({self.name!r}): src resolved to "
                    f"{type(target).__name__}, expected an Artifact"
                )
            return target.output_path(ctx)
        assert self._src_path is not None
        return self._src_path

    def _tracked_files(self, ctx: "BuildContext") -> list[Path]:
        """Every regular file under the resolved src — folded into
        Command.inputs so edits invalidate the cache. For Target/Ref
        sources the upstream's output may not yet exist at build_cmds
        time; the upstream's own stamp covers internal change."""
        src = self._resolve_src(ctx) if self._src_spec is not None else self._src_path
        assert src is not None
        if not src.is_dir():
            return []
        return sorted(p for p in src.rglob("*") if p.is_file())

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        src = self._resolve_src(ctx)
        dst = self.output_path(ctx)
        sq_src = shlex.quote(str(src))
        sq_dst = shlex.quote(str(dst))
        sq_dst_parent = shlex.quote(str(dst.parent))
        lines = [
            "set -e",
            f"rm -rf {sq_dst}",
            f"mkdir -p {sq_dst_parent}",
            f"mkdir -p {sq_dst}",
            f"cp -a {sq_src}/. {sq_dst}/",
        ]
        if self._file_mode is not None:
            lines.append(
                f"find {sq_dst} -type f -exec chmod {self._file_mode} {{}} +"
            )
        if self._dir_mode is not None:
            lines.append(
                f"find {sq_dst} -type d -exec chmod {self._dir_mode} {{}} +"
            )
        prelude = inline_ref_build_cmds(
            [self._src_spec] if isinstance(self._src_spec, Ref) else [],
            ctx,
        )
        return [
            *prelude,
            Command.shell_cmd(
                "\n".join(lines),
                cwd=self.project.root,
                label=f"copy dir {src.name} -> {self.name}",
                inputs=(src, *self._tracked_files(ctx), *self._extra_inputs),
                outputs=(dst,),
            ),
        ]

    def describe(self) -> str:
        if self._src_path is not None:
            src_str = str(self._src_path)
        elif isinstance(self._src_spec, Target):
            src_str = self._src_spec.qualified_name
        elif isinstance(self._src_spec, Ref):
            src_str = self._src_spec.to_spec()
        else:
            src_str = "?"
        return (
            f"DirectoryArtifact {self.qualified_name} ({self.arch})\n"
            f"  src:  {src_str}\n"
            f"  dest: {self._dest or self.name}"
        )
