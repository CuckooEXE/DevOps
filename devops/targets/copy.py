"""FileArtifact, DirectoryArtifact — verbatim copies into the build tree.

Reach for these when something the build needs to ship — a config file,
an asset directory, a static manifest — has to land under build/
alongside compiled artifacts. Both accept either a filesystem path or
another Artifact whose output_path resolves at build time.

For arbitrary transformations, use CustomArtifact instead.

Implementation: copies are performed by ``devops/targets/_copy_runner.py``
invoked under ``sys.executable``. That keeps cp / mkdir / chmod / find
off the required-tools list, sidesteps BSD-vs-GNU differences in those
flags, and turns each copy step into an argv-form Command (cleaner
cache key, cleaner dry-run output) instead of a shell pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from devops.core.command import Command
from devops.core.target import Artifact, DepKind, Target
from devops.remote import Ref
from devops.targets._paths import validate_octal_mode, validate_relative_path
from devops.targets._specs import coerce_source, ref_prelude_for

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
        if dest is not None:
            validate_relative_path(dest, "dest", ident)
        validate_octal_mode(mode, "mode", ident)
        self._src = coerce_source(
            src, kwarg="src", ident=ident,
            project_root=self.project.root,
            deps=self.deps, dep_kind=DepKind.COPY,
        )
        self._dest = dest
        self._mode = mode

    def output_path(self, ctx: "BuildContext") -> Path:
        dest_name = self._dest or self._src.resolve(
            ctx, kwarg="src", ident=f"FileArtifact({self.name!r})",
        ).name
        return self.output_dir(ctx) / dest_name

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        ident = f"FileArtifact({self.name!r})"
        src = self._src.resolve(ctx, kwarg="src", ident=ident)
        dst = self.output_path(ctx)
        argv: list[str] = [
            sys.executable,
            "-m", "devops.targets._copy_runner",
            "file",
            "--src", str(src),
            "--dst", str(dst),
        ]
        if self._mode is not None:
            argv.extend(["--chmod", self._mode])
        return [
            *ref_prelude_for([self._src], ctx),
            Command(
                argv=tuple(argv),
                cwd=self.project.root,
                label=f"copy {src.name} -> {self.name}",
                inputs=(src, *self._extra_inputs),
                outputs=(dst,),
            ),
        ]

    def describe(self) -> str:
        return (
            f"FileArtifact {self.qualified_name} ({self.arch})\n"
            f"  src:  {self._src.describe_str()}\n"
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
        if dest is not None:
            validate_relative_path(dest, "dest", ident)
        validate_octal_mode(file_mode, "file_mode", ident)
        validate_octal_mode(dir_mode, "dir_mode", ident)
        self._src = coerce_source(
            src, kwarg="src", ident=ident,
            project_root=self.project.root,
            deps=self.deps, dep_kind=DepKind.COPY,
        )
        # For raw filesystem sources, validate at config time that the
        # path is a directory — Target/Ref sources can't be inspected
        # yet because they materialize at build time.
        if self._src.path is not None:
            p = self._src.path
            if not p.exists():
                raise FileNotFoundError(f"{ident}: src={p} does not exist")
            if not p.is_dir():
                raise NotADirectoryError(f"{ident}: src={p} is not a directory")
        self._dest = dest
        self._file_mode = file_mode
        self._dir_mode = dir_mode

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / (self._dest or self.name)

    def _tracked_files(self, ctx: "BuildContext") -> list[Path]:
        """Every regular file under the resolved src — folded into
        Command.inputs so edits invalidate the cache. For Target/Ref
        sources the upstream's output may not yet exist at build_cmds
        time; the upstream's own stamp covers internal change."""
        src = self._src.resolve(
            ctx, kwarg="src", ident=f"DirectoryArtifact({self.name!r})",
        )
        if not src.is_dir():
            return []
        return sorted(p for p in src.rglob("*") if p.is_file())

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        ident = f"DirectoryArtifact({self.name!r})"
        src = self._src.resolve(ctx, kwarg="src", ident=ident)
        dst = self.output_path(ctx)
        argv: list[str] = [
            sys.executable,
            "-m", "devops.targets._copy_runner",
            "dir",
            "--src", str(src),
            "--dst", str(dst),
        ]
        if self._file_mode is not None:
            argv.extend(["--file-mode", self._file_mode])
        if self._dir_mode is not None:
            argv.extend(["--dir-mode", self._dir_mode])
        return [
            *ref_prelude_for([self._src], ctx),
            Command(
                argv=tuple(argv),
                cwd=self.project.root,
                label=f"copy dir {src.name} -> {self.name}",
                inputs=(src, *self._tracked_files(ctx), *self._extra_inputs),
                outputs=(dst,),
            ),
        ]

    def describe(self) -> str:
        return (
            f"DirectoryArtifact {self.qualified_name} ({self.arch})\n"
            f"  src:  {self._src.describe_str()}\n"
            f"  dest: {self._dest or self.name}"
        )
