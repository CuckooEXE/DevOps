"""C/C++ artifact types and the CCompile mixin.

CCompile centralises flag composition. `_compile_flags(src)` returns the
exact flags passed to the compiler — reused verbatim by the lint tools
(clang-tidy, cppcheck) so the user never restates flags.

Flag composition vs tool invocation is deliberately split:
    _compile_flags(src)    # ("-O0", "-ggdb", "-I./include", "-DFOO=1", ...)
    _compile_argv(src)     # (*ctx.toolchain.cc.argv, *_compile_flags, "-c", src, "-o", obj)
"""

from __future__ import annotations

import glob
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Union

from devops.core.command import Command
from devops.core.target import Artifact, DepKind, Project, Target
from devops.options import OptimizationLevel
from devops.remote import Ref

if TYPE_CHECKING:
    from devops.context import BuildContext


# `Sequence` is covariant, so `list[Path]` (what glob() returns) fits into
# `Sequence[str | Path]`. `list[str | Path]` here would force callers to
# build list[str | Path] explicitly — that's the VSCode noise the user hit.
SourcesSpec = Union[str, Path, Sequence[Union[str, Path]]]
# A Target object, "::name" (local), "name" (system lib -Llinker), or a typed
# Ref (GitRef / TarballRef / DirectoryRef) for remote projects.
LibSpec = Union[str, Target, Ref]
# includes= accepts raw paths (bare dirs) plus Target / Ref entries that
# materialize to a -I<output_dir> at compile time. Only HeadersOnly (or a
# Ref resolving to one) is semantically valid; non-HeadersOnly targets raise
# TypeError during _compile_flags.
IncludeEntry = Union[str, Path, Target, Ref]
IncludesSpec = Union[IncludeEntry, Sequence[IncludeEntry]]


def _as_sequence(specs: SourcesSpec) -> Sequence[str | Path]:
    if isinstance(specs, (str, Path)):
        return [specs]
    return specs


def _resolve_sources(project_root: Path, specs: SourcesSpec | None) -> list[Path]:
    """Literal-path resolution.

    Strings/Paths here are *not* glob-expanded — use `builder.glob(...)` for
    globbing. This makes globbing an explicit, auditable step.
    """
    if specs is None:
        return []
    paths: list[Path] = []
    seen: set[Path] = set()
    for s in _as_sequence(specs):
        p = Path(s)
        if not p.is_absolute():
            p = (project_root / p).resolve()
        if "*" in str(p):
            raise ValueError(
                f"glob pattern {s!r} cannot be used directly — wrap it in builder.glob(...)"
            )
        if not p.exists():
            raise FileNotFoundError(f"source not found: {s} (looked at {p})")
        if p not in seen:
            seen.add(p)
            paths.append(p)
    return paths


def _include_label(inc: IncludeEntry) -> str:
    """Render one ``includes=`` entry for ``describe()`` output."""
    if isinstance(inc, Ref):
        return inc.to_spec()
    if isinstance(inc, Target):
        return inc.qualified_name
    return str(inc)


def _resolve_includes(
    project_root: Path,
    specs: IncludesSpec | None,
) -> list[IncludeEntry]:
    """Normalize `includes=` entries.

    Bare strings/Paths are validated + resolved against ``project_root``
    (same rules as ``_resolve_sources``). Target and Ref entries are kept
    as-is and materialized to a directory by ``_compile_flags`` at
    compile time — deferring gives us a ``BuildContext`` for the
    Target's ``output_path(ctx)`` (and for resolving remote Refs).
    """
    if specs is None:
        return []
    items: Sequence[IncludeEntry]
    if isinstance(specs, (str, Path, Target, Ref)):
        items = [specs]
    else:
        items = list(specs)
    out: list[IncludeEntry] = []
    seen_paths: set[Path] = set()
    for s in items:
        if isinstance(s, (Target, Ref)):
            out.append(s)
            continue
        p = Path(s)
        if not p.is_absolute():
            p = (project_root / p).resolve()
        if "*" in str(p):
            raise ValueError(
                f"glob pattern {s!r} cannot be used directly — wrap it in builder.glob(...)"
            )
        if not p.exists():
            raise FileNotFoundError(f"include not found: {s} (looked at {p})")
        if p not in seen_paths:
            seen_paths.add(p)
            out.append(p)
    return out


def glob_sources(
    project_root: Path,
    patterns: SourcesSpec,
    exclude: SourcesSpec | None = None,
    allow_empty: bool = False,
) -> list[Path]:
    """Bazel-style glob. Returns files matching any pattern, minus `exclude`."""
    matched: set[Path] = set()
    for pat in _as_sequence(patterns):
        full = str((project_root / Path(pat)).resolve())
        for h in glob.glob(full, recursive=True):
            p = Path(h)
            if p.is_file():
                matched.add(p)

    excluded: set[Path] = set()
    if exclude is not None:
        for pat in _as_sequence(exclude):
            full = str((project_root / Path(pat)).resolve())
            for h in glob.glob(full, recursive=True):
                excluded.add(Path(h))

    result = sorted(matched - excluded)
    if not result and not allow_empty:
        raise FileNotFoundError(
            f"glob({patterns!r}) matched nothing; pass allow_empty=True to permit."
        )
    return result


class CCompile:
    """Mixin: shared flag computation for C-family artifacts.

    Always combined with `Artifact` in concrete classes — the attribute
    declarations below are shadowed in __init__ of the combined class; the
    annotations here exist so static type checkers see them.
    """

    # From Target / Artifact (set by their __init__):
    name: str
    project: Project
    deps: dict[str, Target]
    arch: str

    # Set by the combined class's __init__:
    srcs: list[Path]
    includes: list[IncludeEntry]
    flags: tuple[str, ...]
    defs: dict[str, str | None]
    undefs: tuple[str, ...]
    libs: tuple[LibSpec, ...]
    is_cxx: bool
    _pic: bool

    def _profile_flags(self, profile: OptimizationLevel) -> tuple[str, ...]:
        return profile.cflags

    def _include_dir(self, inc: IncludeEntry, ctx: "BuildContext") -> Path:
        """Materialize one ``includes=`` entry to a directory for ``-I``.

        Plain paths pass through; Target and Ref entries must be (or
        resolve to) a ``HeadersOnly`` whose ``output_path(ctx)`` names
        the staged ``include/`` directory.
        """
        if isinstance(inc, Ref):
            from devops.remote import resolve_remote_ref

            target: Target = resolve_remote_ref(inc)
        elif isinstance(inc, Target):
            target = inc
        else:
            return Path(inc)  # str/Path — coerce
        if not isinstance(target, HeadersOnly):
            raise TypeError(
                f"includes= only supports HeadersOnly targets "
                f"(or Refs resolving to one); got "
                f"{type(target).__name__} for {inc!r}"
            )
        return target.output_path(ctx)

    def _compile_flags(self, ctx: "BuildContext") -> tuple[str, ...]:
        """Flags independent of the source being compiled.

        clang-tidy / cppcheck consume these verbatim.
        """
        out: list[str] = []
        out.extend(self._profile_flags(ctx.profile))
        for inc in self.includes:
            out.append(f"-I{self._include_dir(inc, ctx)}")
        for k, v in self.defs.items():
            out.append(f"-D{k}" if v is None else f"-D{k}={v}")
        for k in self.undefs:
            out.append(f"-U{k}")
        out.extend(self.flags)
        if getattr(self, "_pic", False):
            out.append("-fPIC")
        return tuple(out)

    def _obj_path(self, src: Path, ctx: "BuildContext", out_dir: Path) -> Path:
        # Flatten source path relative to project into a dotted stem to avoid collisions
        try:
            rel = src.relative_to(self.project.root)
        except ValueError:
            rel = Path(src.name)
        stem = str(rel.with_suffix("")).replace("/", ".")
        return out_dir / "obj" / f"{stem}.o"

    def _compile_command(self, src: Path, ctx: "BuildContext", out_dir: Path) -> Command:
        obj = self._obj_path(src, ctx, out_dir)
        depfile = obj.with_suffix(obj.suffix + ".d")
        tc = ctx.toolchain_for(self.arch)
        tool = tc.cxx if self.is_cxx else tc.cc
        tool = tool.resolved_for(
            workspace=ctx.workspace_root,
            project=self.project.root,
            cwd=self.project.root,
        )
        # -MMD: emit a Makefile-style depfile listing user-header #includes
        # (skipping system headers). -MF picks the output location. The
        # cache reads this on the *next* invocation to detect header
        # changes that don't otherwise touch the .c file mtime.
        argv = tool.invoke([
            *self._compile_flags(ctx),
            "-MMD", "-MF", str(depfile),
            "-c", str(src),
            "-o", str(obj),
        ])
        return Command(
            argv=argv,
            cwd=self.project.root,
            label=f"compile {src.name}",
            inputs=(src,),
            outputs=(obj,),
            depfile=depfile,
        )

    def _compile_all(self, ctx: "BuildContext", out_dir: Path) -> tuple[list[Command], list[Path]]:
        cmds: list[Command] = []
        objs: list[Path] = []
        for src in self.srcs:
            cmd = self._compile_command(src, ctx, out_dir)
            cmds.append(cmd)
            objs.append(cmd.outputs[0])
        return cmds, objs

    def _remote_dep_build_cmds(self, ctx: "BuildContext") -> list[Command]:
        """Inline build commands for any remote Target referenced by
        ``libs=`` or ``includes=``. Refs aren't in ``self.deps``
        (resolution is lazy and network-backed), so we have to schedule
        them ourselves. Routes through the shared
        ``inline_ref_build_cmds`` so the per-run dedup spans every
        artifact in the build, not just this one."""
        from devops.targets._specs import inline_ref_build_cmds

        refs = [
            entry
            for entries in (self.libs, self.includes)
            for entry in entries
            if isinstance(entry, Ref)
        ]
        return inline_ref_build_cmds(refs, ctx)

    def _link_flags_for_libs(self, ctx: "BuildContext") -> tuple[list[str], list[Path]]:
        """Return (linker args, extra input paths).

        Adds an rpath for each linked shared object so the produced binary
        can locate its libs at runtime without requiring LD_LIBRARY_PATH.
        """
        from devops import registry

        args: list[str] = []
        extra_inputs: list[Path] = []
        rpaths: set[str] = set()
        for spec in self.libs:
            if isinstance(spec, Target):
                lib_target = spec
            elif isinstance(spec, Ref):
                from devops.remote import resolve_remote_ref

                lib_target = resolve_remote_ref(spec)
            elif isinstance(spec, str) and spec.startswith("::"):
                lib_target = registry.resolve(spec, current=self.project)
            elif isinstance(spec, str):
                if "://" in spec:
                    raise TypeError(
                        f"remote libs must use a typed Ref "
                        f"(GitRef / TarballRef / DirectoryRef); got {spec!r}"
                    )
                args.append(f"-l{spec}")
                continue
            else:
                raise TypeError(f"bad lib spec: {spec!r}")

            if not isinstance(lib_target, Artifact):
                raise TypeError(f"cannot link against non-Artifact: {lib_target!r}")
            out = lib_target.output_path(ctx)
            if isinstance(lib_target, ElfSharedObject):
                args.extend([f"-L{out.parent}", f"-l{lib_target.name}"])
                rpaths.add(str(out.parent))
                extra_inputs.append(out)
            elif isinstance(lib_target, StaticLibrary):
                args.append(str(out))
                extra_inputs.append(out)
            else:
                raise TypeError(f"cannot link against {type(lib_target).__name__}")
        for rp in sorted(rpaths):
            args.append(f"-Wl,-rpath,{rp}")
        return args, extra_inputs


class ElfBinary(CCompile, Artifact):
    def __init__(
        self,
        name: str,
        srcs: SourcesSpec,
        includes: IncludesSpec | None = None,
        flags: tuple[str, ...] = (),
        defs: dict[str, str | None] | None = None,
        undefs: tuple[str, ...] | list[str] = (),
        libs: tuple[LibSpec, ...] | list[LibSpec] = (),
        is_cxx: bool = False,
        tests: "dict[str, object] | None" = None,
        version: str | None = None,
        deps: dict[str, Target] | None = None,
        doc: str | None = None,
        arch: str = "host",
        extra_inputs: "tuple[str | Path, ...] | list[str | Path] | None" = None,
    ) -> None:
        super().__init__(
            name=name, deps=deps, version=version, doc=doc, arch=arch,
            extra_inputs=extra_inputs,
        )
        self.srcs = _resolve_sources(self.project.root, srcs)
        self.includes = _resolve_includes(self.project.root, includes)
        self.flags = tuple(flags)
        self.defs = dict(defs or {})
        self.undefs = tuple(undefs)
        self.libs = tuple(libs)
        self.is_cxx = is_cxx
        self._pic = False
        # Targets in libs= / includes= flow into deps so topo-sort builds them first
        for spec in self.libs:
            if isinstance(spec, Target):
                self.register_dep(DepKind.LIB, spec)
        for inc in self.includes:
            if isinstance(inc, Target):
                self.register_dep(DepKind.INCLUDE, inc)
        if tests is not None:
            from devops.targets.tests import GoogleTest

            GoogleTest(name=f"{name}Tests", target=self, **tests)  # type: ignore[arg-type]

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / self.name

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        out_dir = self.output_dir(ctx)
        remote_cmds = self._remote_dep_build_cmds(ctx)
        compile_cmds, objs = self._compile_all(ctx, out_dir)
        lib_args, extra_inputs = self._link_flags_for_libs(ctx)
        tc = ctx.toolchain_for(self.arch)
        tool = (tc.cxx if self.is_cxx else tc.cc).resolved_for(
            workspace=ctx.workspace_root, project=self.project.root, cwd=self.project.root
        )
        link_argv = tool.invoke([*(str(o) for o in objs), *lib_args, "-o", str(self.output_path(ctx))])
        link_cmd = Command(
            argv=link_argv,
            cwd=self.project.root,
            label=f"link {self.name}",
            inputs=(*objs, *extra_inputs, *self._extra_inputs),
            outputs=(self.output_path(ctx),),
        )
        return [*remote_cmds, *compile_cmds, link_cmd]

    def lint_cmds(self, ctx: "BuildContext") -> list[Command]:
        from devops.tools import clang

        return clang.lint_for_ccompile(self, ctx)

    def describe(self) -> str:
        src_list = ", ".join(s.name for s in self.srcs)

        def _lib_label(spec: object) -> str:
            if isinstance(spec, str):
                return spec
            if isinstance(spec, Ref):
                return spec.to_spec()
            name: str = spec.qualified_name  # type: ignore[attr-defined]
            return name

        lib_list = ", ".join(_lib_label(s) for s in self.libs) or "-"
        inc_list = ", ".join(_include_label(inc) for inc in self.includes) or "-"
        return (
            f"{type(self).__name__} {self.qualified_name} ({self.arch})\n"
            f"  srcs:     {src_list}\n"
            f"  includes: {inc_list}\n"
            f"  libs:     {lib_list}\n"
            f"  flags:    {' '.join(self.flags) or '-'}"
        )


class ElfSharedObject(ElfBinary):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._pic = True

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / f"lib{self.name}.so"

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        out_dir = self.output_dir(ctx)
        remote_cmds = self._remote_dep_build_cmds(ctx)
        compile_cmds, objs = self._compile_all(ctx, out_dir)
        lib_args, extra_inputs = self._link_flags_for_libs(ctx)
        tc = ctx.toolchain_for(self.arch)
        tool = (tc.cxx if self.is_cxx else tc.cc).resolved_for(
            workspace=ctx.workspace_root, project=self.project.root, cwd=self.project.root
        )
        link_argv = tool.invoke(["-shared", *(str(o) for o in objs), *lib_args, "-o", str(self.output_path(ctx))])
        link_cmd = Command(
            argv=link_argv,
            cwd=self.project.root,
            label=f"link lib{self.name}.so",
            inputs=(*objs, *extra_inputs, *self._extra_inputs),
            outputs=(self.output_path(ctx),),
        )
        return [*remote_cmds, *compile_cmds, link_cmd]


class StaticLibrary(CCompile, Artifact):
    def __init__(
        self,
        name: str,
        srcs: SourcesSpec,
        includes: IncludesSpec | None = None,
        flags: tuple[str, ...] = (),
        defs: dict[str, str | None] | None = None,
        undefs: tuple[str, ...] | list[str] = (),
        is_cxx: bool = False,
        version: str | None = None,
        deps: dict[str, Target] | None = None,
        doc: str | None = None,
        arch: str = "host",
        extra_inputs: "tuple[str | Path, ...] | list[str | Path] | None" = None,
    ) -> None:
        super().__init__(
            name=name, deps=deps, version=version, doc=doc, arch=arch,
            extra_inputs=extra_inputs,
        )
        self.srcs = _resolve_sources(self.project.root, srcs)
        self.includes = _resolve_includes(self.project.root, includes)
        self.flags = tuple(flags)
        self.defs = dict(defs or {})
        self.undefs = tuple(undefs)
        self.libs = ()
        self.is_cxx = is_cxx
        self._pic = False
        for inc in self.includes:
            if isinstance(inc, Target):
                self.register_dep(DepKind.INCLUDE, inc)

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / f"lib{self.name}.a"

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        out_dir = self.output_dir(ctx)
        compile_cmds, objs = self._compile_all(ctx, out_dir)
        ar = ctx.toolchain_for(self.arch).ar.resolved_for(
            workspace=ctx.workspace_root, project=self.project.root, cwd=self.project.root
        )
        ar_argv = ar.invoke(["rcs", str(self.output_path(ctx)), *(str(o) for o in objs)])
        return [
            *compile_cmds,
            Command(
                argv=ar_argv,
                cwd=self.project.root,
                label=f"archive lib{self.name}.a",
                inputs=(*objs, *self._extra_inputs),
                outputs=(self.output_path(ctx),),
            ),
        ]

    def lint_cmds(self, ctx: "BuildContext") -> list[Command]:
        from devops.tools import clang

        return clang.lint_for_ccompile(self, ctx)

    def describe(self) -> str:
        return (
            f"StaticLibrary {self.qualified_name}\n"
            f"  srcs:     {', '.join(s.name for s in self.srcs)}\n"
            f"  includes: "
            f"{', '.join(_include_label(inc) for inc in self.includes) or '-'}"
        )


class CObjectFile(CCompile, Artifact):
    """Compile sources to `.o` files — no linking.

    Output layout::

        <output_dir>/
          obj/<flattened>.o    # one .o per source
          obj/...

    Pass a CObjectFile to ``LdBinary(objs=[...])`` to drive the link step
    separately. This splits the compile/link phases the way classic
    Makefiles do:

        gcc -c main.c -o main.o
        ld  main.o -o main
    """

    def __init__(
        self,
        name: str,
        srcs: SourcesSpec,
        includes: IncludesSpec | None = None,
        flags: tuple[str, ...] = (),
        defs: dict[str, str | None] | None = None,
        undefs: tuple[str, ...] | list[str] = (),
        is_cxx: bool = False,
        pic: bool = False,
        version: str | None = None,
        deps: dict[str, Target] | None = None,
        doc: str | None = None,
        arch: str = "host",
        extra_inputs: "tuple[str | Path, ...] | list[str | Path] | None" = None,
    ) -> None:
        super().__init__(
            name=name, deps=deps, version=version, doc=doc, arch=arch,
            extra_inputs=extra_inputs,
        )
        self.srcs = _resolve_sources(self.project.root, srcs)
        self.includes = _resolve_includes(self.project.root, includes)
        self.flags = tuple(flags)
        self.defs = dict(defs or {})
        self.undefs = tuple(undefs)
        self.libs = ()
        self.is_cxx = is_cxx
        self._pic = pic
        for inc in self.includes:
            if isinstance(inc, Target):
                self.register_dep(DepKind.INCLUDE, inc)

    def output_path(self, ctx: "BuildContext") -> Path:
        # Directory containing the produced .o files.
        return self.output_dir(ctx) / "obj"

    def object_files(self, ctx: "BuildContext") -> list[Path]:
        """The exact .o paths this target produces (one per source)."""
        out_dir = self.output_dir(ctx)
        return [self._obj_path(src, ctx, out_dir) for src in self.srcs]

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        out_dir = self.output_dir(ctx)
        compile_cmds, _ = self._compile_all(ctx, out_dir)
        return compile_cmds

    def lint_cmds(self, ctx: "BuildContext") -> list[Command]:
        from devops.tools import clang

        return clang.lint_for_ccompile(self, ctx)

    def describe(self) -> str:
        return (
            f"CObjectFile {self.qualified_name} ({self.arch})\n"
            f"  srcs:     {', '.join(s.name for s in self.srcs)}\n"
            f"  includes: "
            f"{', '.join(_include_label(inc) for inc in self.includes) or '-'}"
        )


class LdBinary(Artifact):
    """Link objects into a binary via ``ld`` directly (no cc driver).

    Use this for freestanding / embedded / bootloader-style builds where
    you control every linker flag. For normal userspace binaries prefer
    ``ElfBinary`` (drives cc, which handles libc startup files, default
    search paths, rpath, etc.).

    Args:
        name:           target / filename
        objs:           list of CObjectFile targets, static archives
                        (``Path``), or literal strings (``-lfoo``, etc.)
        linker_script:  path to a linker script (``-T <script>``). Flows
                        into cache inputs so edits invalidate the link.
        map_file:       filename to emit a linker map as (``-Map <path>``).
                        Lives alongside the binary under ``output_dir``.
        entry:          entry symbol (``-e <sym>``), e.g. ``"_start"``.
        extra_ld_flags: appended verbatim before the object list.
    """

    def __init__(
        self,
        name: str,
        objs: list[CObjectFile | str | Path],
        linker_script: str | Path | None = None,
        map_file: str | None = None,
        entry: str | None = None,
        extra_ld_flags: tuple[str, ...] = (),
        libs: "tuple[str | Path | Target, ...] | list[str | Path | Target]" = (),
        version: str | None = None,
        deps: dict[str, Target] | None = None,
        doc: str | None = None,
        arch: str = "host",
        extra_inputs: "tuple[str | Path, ...] | list[str | Path] | None" = None,
    ) -> None:
        super().__init__(
            name=name, deps=deps, version=version, doc=doc, arch=arch,
            extra_inputs=extra_inputs,
        )
        self.objs = tuple(objs)
        self.map_file = map_file
        self.entry = entry
        self.extra_ld_flags = tuple(extra_ld_flags)
        self.libs = tuple(libs)

        self.linker_script: Path | None = None
        if linker_script is not None:
            p = Path(linker_script)
            if not p.is_absolute():
                p = (self.project.root / p).resolve()
            self.linker_script = p

        # Targets in `objs` or `libs` flow into deps so topo-sort builds
        # them before this ld step.
        for o in self.objs:
            if isinstance(o, Target):
                self.register_dep(DepKind.OBJ, o)
        for lib in self.libs:
            if isinstance(lib, Target):
                self.register_dep(DepKind.LIB, lib)

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / self.name

    def map_path(self, ctx: "BuildContext") -> Path | None:
        return self.output_dir(ctx) / self.map_file if self.map_file else None

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        obj_args: list[str] = []
        inputs: list[Path] = []

        for o in self.objs:
            if isinstance(o, CObjectFile):
                for f in o.object_files(ctx):
                    obj_args.append(str(f))
                    inputs.append(f)
            elif isinstance(o, Path) or (isinstance(o, str) and not o.startswith("-")):
                p = Path(o)
                if not p.is_absolute():
                    p = (self.project.root / p).resolve()
                obj_args.append(str(p))
                inputs.append(p)
            elif isinstance(o, str):
                # Literal flag-form token like `-lfoo`, `--whole-archive`, etc.
                obj_args.append(o)
            else:
                raise TypeError(f"bad objs entry: {o!r}")

        lib_args: list[str] = []
        for lib in self.libs:
            if isinstance(lib, StaticLibrary):
                lib_path = lib.output_path(ctx)
                lib_args.append(str(lib_path))
                inputs.append(lib_path)
            elif isinstance(lib, str):
                lib_args.append(lib if lib.startswith("-") else f"-l{lib}")
            elif isinstance(lib, Path):
                lib_args.append(str(lib))
                inputs.append(lib)
            else:
                raise TypeError(
                    f"LdBinary libs must be StaticLibrary / str / Path, got {type(lib).__name__}"
                )

        out = self.output_path(ctx)
        argv_parts: list[str] = []
        if self.entry:
            argv_parts.extend(["-e", self.entry])
        if self.linker_script is not None:
            argv_parts.extend(["-T", str(self.linker_script)])
            inputs.append(self.linker_script)
        map_path = self.map_path(ctx)
        if map_path is not None:
            argv_parts.extend(["-Map", str(map_path)])
        argv_parts.extend(self.extra_ld_flags)
        argv_parts.extend(obj_args)
        argv_parts.extend(lib_args)
        argv_parts.extend(["-o", str(out)])

        ld = ctx.toolchain_for(self.arch).ld.resolved_for(
            workspace=ctx.workspace_root,
            project=self.project.root,
            cwd=self.project.root,
        )
        outputs: tuple[Path, ...] = (out,) if map_path is None else (out, map_path)
        return [
            Command(
                argv=ld.invoke(argv_parts),
                cwd=self.project.root,
                label=f"ld {self.name}",
                inputs=(*inputs, *self._extra_inputs),
                outputs=outputs,
            )
        ]

    def describe(self) -> str:
        obj_summary = ", ".join(
            o.name if isinstance(o, Target) else str(o) for o in self.objs
        ) or "-"
        return (
            f"LdBinary {self.qualified_name} ({self.arch})\n"
            f"  objs:          {obj_summary}\n"
            f"  linker_script: {self.linker_script or '-'}\n"
            f"  map_file:      {self.map_file or '-'}\n"
            f"  entry:         {self.entry or '-'}"
        )


class HeadersOnly(Artifact):
    """A header bundle other targets can pick up as includes.

    Headers are staged under ``output_path(ctx)`` preserving their path
    relative to the project root. If your source tree keeps public
    headers under a wrapper directory (e.g. ``include/``) and your
    consumers write ``#include "mylib.h"`` (not ``"include/mylib.h"``),
    pass ``strip_prefix="include"`` so that wrapper is dropped during
    staging — the consumer's ``-I<staged dir>`` will then resolve the
    bare header name.
    """

    def __init__(
        self,
        name: str,
        srcs: SourcesSpec,
        strip_prefix: str | Path = "",
        deps: dict[str, Target] | None = None,
        doc: str | None = None,
    ) -> None:
        super().__init__(name=name, deps=deps, doc=doc)
        self.headers = _resolve_sources(self.project.root, srcs)
        self.strip_prefix = Path(strip_prefix) if strip_prefix else None

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / "include"

    def _staged_path(self, h: Path, out: Path) -> Path:
        rel = h.relative_to(self.project.root)
        if self.strip_prefix is not None:
            try:
                rel = rel.relative_to(self.strip_prefix)
            except ValueError:
                raise ValueError(
                    f"HeadersOnly {self.name}: strip_prefix="
                    f"{str(self.strip_prefix)!r} doesn't match header {rel}"
                )
        return out / rel

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        out = self.output_path(ctx)
        staged = [(h, self._staged_path(h, out)) for h in self.headers]
        # The runner's _ensure_output_parents creates each cp's
        # outputs[0].parent before invocation, so a separate mkdir step
        # is redundant — and harmful, because that step had no
        # outputs= and so was always cache-stale.
        cmds: list[Command] = []
        for h, dst in staged:
            cmds.append(
                Command(
                    argv=("cp", str(h), str(dst)),
                    label=f"stage {h.name}",
                    inputs=(h,),
                    outputs=(dst,),
                )
            )
        return cmds

    def describe(self) -> str:
        return f"HeadersOnly {self.qualified_name}: {len(self.headers)} header(s)"
