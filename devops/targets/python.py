"""Python artifact types:

- ``PythonWheel``   — builds a wheel via ``python -m build --wheel``.
- ``PythonApp``     — runnable Python package; auto-managed venv per target,
                      honours ``requirements.txt``. Use for development and
                      local execution.
- ``PythonShiv``    — single-file ``.pyz`` archive (via ``shiv``) with
                      dependencies baked in. Use for delivery to boxes that
                      may not have pip / a package index.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from devops.core.command import Command
from devops.core.target import Artifact, DepKind, Target
from devops.remote import Ref
from devops.targets.c_cpp import SourcesSpec, _resolve_sources

if TYPE_CHECKING:
    from devops.context import BuildContext


class PythonWheel(Artifact):
    """Produces dist/<name>-<version>-py3-none-any.whl via `python -m build`.

    Expects a pyproject.toml in the project root (or `pyproject=` override).
    The tests= kwarg desugars to a Pytest target named "<name>Tests".
    """

    def __init__(
        self,
        name: str,
        srcs: SourcesSpec | None = None,
        pyproject: str | Path = "pyproject.toml",
        tests: dict[str, object] | None = None,
        version: str | None = None,
        deps: dict[str, Target] | None = None,
        doc: str | None = None,
    ) -> None:
        super().__init__(name=name, deps=deps, version=version, doc=doc)
        self.srcs = _resolve_sources(self.project.root, srcs) if srcs else []
        self.pyproject = self.project.root / pyproject
        if tests is not None:
            from devops.targets.tests import Pytest

            Pytest(name=f"{name}Tests", target=self, **tests)  # type: ignore[arg-type]

    def output_path(self, ctx: "BuildContext") -> Path:
        # Actual filename is "<dist-name>-<version>-py3-none-any.whl" where
        # <dist-name> comes from pyproject.toml. We don't parse it — return
        # the containing dir so downstream consumers glob for *.whl.
        return self.output_dir(ctx) / "dist"

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        # `python -m build` looks for pyproject.toml in cwd; run it from the
        # dir containing the wheel's pyproject, not the project root.
        wheel_cwd = self.pyproject.parent
        python = ctx.toolchain.python.resolved_for(
            workspace=ctx.workspace_root, project=self.project.root, cwd=wheel_cwd
        )
        out_dir = self.output_path(ctx)
        return [
            Command(
                argv=python.invoke(["-m", "build", "--wheel", "--outdir", str(out_dir)]),
                cwd=wheel_cwd,
                # SOURCE_DATE_EPOCH=0 makes python -m build (and the
                # underlying setuptools / hatchling / flit) bake a fixed
                # epoch into the wheel's RECORD timestamps and zip
                # entries — without it, every rebuild produces fresh
                # bytes even when inputs are unchanged.
                env=(("SOURCE_DATE_EPOCH", "0"),),
                label=f"build wheel {self.name}",
                inputs=(self.pyproject, *self.srcs),
                outputs=(out_dir,),
            )
        ]

    def lint_cmds(self, ctx: "BuildContext") -> list[Command]:
        from devops.tools import python_tools

        return python_tools.lint_for_python(self, ctx)

    def describe(self) -> str:
        return (
            f"PythonWheel {self.qualified_name}\n"
            f"  pyproject: {self.pyproject}\n"
            f"  srcs:      {len(self.srcs)} file(s)"
        )


# ---------------------------------------------------------------------------
# PythonApp
# ---------------------------------------------------------------------------


def _default_requirements(project_root: Path) -> Path | None:
    """Default to ``<project>/requirements.txt`` when it exists."""
    p = project_root / "requirements.txt"
    return p if p.is_file() else None


# ---------------------------------------------------------------------------
# python_deps resolution
# ---------------------------------------------------------------------------


def _resolve_python_dep(
    spec: "PythonWheel | str | Ref",
    project: "object",
) -> "PythonWheel":
    """Resolve one python_deps= entry to a PythonWheel.

    Accepts:
      - a PythonWheel target instance (monorepo case)
      - ``"::name"``  — local ref, must already be in the registry
      - a typed ``Ref`` — GitRef / TarballRef / DirectoryRef
    """
    from devops import registry
    from devops.remote import Ref, resolve_remote_ref

    if isinstance(spec, PythonWheel):
        return spec
    if isinstance(spec, Ref):
        target = resolve_remote_ref(spec)
    elif isinstance(spec, str):
        if not spec.startswith("::"):
            raise ValueError(
                f"python_deps string {spec!r} must be '::name' (local). "
                f"For remote wheels use GitRef / TarballRef / DirectoryRef."
            )
        target = registry.resolve(spec, current=project)  # type: ignore[arg-type]
    else:
        raise TypeError(
            f"python_deps entries must be PythonWheel, str, or Ref; got "
            f"{type(spec).__name__}"
        )
    if not isinstance(target, PythonWheel):
        raise TypeError(
            f"python_deps must resolve to a PythonWheel; {spec!r} "
            f"resolved to {type(target).__name__}"
        )
    return target


class PythonApp(Artifact):
    """A runnable Python package. First ``devops run`` creates a cached venv.

    Layout under ``<output_dir>``:

        <output_dir>/
          <name>           # wrapper shell script — the thing `devops run` execs
          venv/            # cached venv; rebuilt when requirements.txt changes
          .venv-key        # stamp ensuring venv matches current inputs

    Args:
        name:         target name (also wrapper filename)
        entry:        how to invoke the app. Either:
                      - ``"module:function"``   (like an entry point)
                      - ``"script.py"``         (path to a script, relative
                        to the project or absolute)
        pyproject:    optional path to a pyproject.toml. If set, the venv
                      gets ``pip install -e <dir>`` (editable install), so
                      source edits take effect without a venv rebuild.
        requirements: path to requirements.txt. Defaults to
                      ``<project>/requirements.txt`` if it exists.
        srcs:         purely for cache tracking — source files that don't
                      live under pyproject's include paths can be listed
                      here so edits still invalidate downstream builds.
        use_venv:     if False, skip venv management entirely and run
                      against the host Python. Build becomes a no-op;
                      it's the user's job to have deps installed.
    """

    def __init__(
        self,
        name: str,
        entry: str,
        pyproject: str | Path | None = None,
        requirements: str | Path | None = None,
        srcs: SourcesSpec | None = None,
        use_venv: bool = True,
        python_deps: "list[PythonWheel | str | Ref] | None" = None,
        version: str | None = None,
        deps: dict[str, Target] | None = None,
        doc: str | None = None,
    ) -> None:
        super().__init__(name=name, deps=deps, version=version, doc=doc)
        self.entry = entry
        self.use_venv = use_venv
        self.pyproject = (self.project.root / pyproject).resolve() if pyproject else None
        self.requirements: Path | None = (
            (self.project.root / requirements).resolve()
            if requirements is not None
            else _default_requirements(self.project.root)
        )
        self.srcs = _resolve_sources(self.project.root, srcs) if srcs else []
        self._python_deps_spec: list[PythonWheel | str | Ref] = list(python_deps or [])

        # Monorepo case: any PythonWheel passed as a Target-instance flows
        # into deps so topo-sort builds it before this app. String-form
        # (local "::name" or remote "url::name") resolves lazily at
        # build_cmds time because remote refs may need a network fetch
        # we don't want to do at build.py import.
        for d in self._python_deps_spec:
            if isinstance(d, PythonWheel):
                self.register_dep(DepKind.PYDEP, d)

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / self.name

    def _venv_dir(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / "venv"

    def _venv_python(self, ctx: "BuildContext") -> Path:
        return self._venv_dir(ctx) / "bin" / "python"

    def _venv_key_file(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / ".venv-key"

    def python_deps(self) -> list["PythonWheel"]:
        """Resolve every python_deps= entry to a PythonWheel (lazy)."""
        return [_resolve_python_dep(d, self.project) for d in self._python_deps_spec]

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        out = self.output_dir(ctx)
        wrapper = self.output_path(ctx)

        # Build every python_dep first. Remote wheels aren't in topo-order
        # because resolution is deferred, so prepend explicitly. Cached
        # wheels no-op via stamp; no work wasted.
        dep_wheels = self.python_deps()
        dep_build_cmds: list[Command] = []
        for w in dep_wheels:
            dep_build_cmds.extend(w.build_cmds(ctx))

        if not self.use_venv:
            return dep_build_cmds + [self._write_wrapper_cmd(ctx, wrapper, venv_python=None)]

        # Otherwise, create / refresh the venv then write the wrapper.
        host_python = ctx.toolchain.python.resolved_for(
            workspace=ctx.workspace_root, project=self.project.root, cwd=self.project.root,
        )
        venv = self._venv_dir(ctx)
        key_file = self._venv_key_file(ctx)

        key_inputs = [str(host_python.argv[0])]
        if self.requirements and self.requirements.is_file():
            key_inputs.append(self.requirements.read_text() if self.requirements.exists() else "")
        key_inputs.append(self.entry)
        # The stamp file below handles invalidation — but we also gate the
        # `rm -rf venv` on a user-visible key so a half-built venv after a
        # crash doesn't linger.
        ensure_script_lines = [
            "set -e",
            f"mkdir -p {out}",
            f"KEY_FILE={key_file}",
            f"NEW_KEY=$(printf %s {' '.join(repr(k) for k in key_inputs)} | sha256sum | cut -c1-16)",
            f'if [ ! -d {venv} ] || [ "$(cat $KEY_FILE 2>/dev/null || true)" != "$NEW_KEY" ]; then',
            f"  rm -rf {venv}",
            f"  {' '.join(host_python.argv)} -m venv {venv}",
            f"  {venv}/bin/pip install --quiet --upgrade pip",
        ]
        if self.requirements and self.requirements.is_file():
            ensure_script_lines.append(
                f"  {venv}/bin/pip install --quiet -r {self.requirements}"
            )
        # Install every python_dep wheel. Shell globs *.whl so we don't
        # need to know versions at config time.
        for wheel in dep_wheels:
            wheel_dir = wheel.output_path(ctx)
            ensure_script_lines.append(
                f"  {venv}/bin/pip install --quiet --force-reinstall {wheel_dir}/*.whl"
            )
        if self.pyproject:
            ensure_script_lines.append(
                f"  {venv}/bin/pip install --quiet -e {self.pyproject.parent}"
            )
        ensure_script_lines.extend([
            "  printf %s \"$NEW_KEY\" > $KEY_FILE",
            "fi",
        ])
        ensure_script = "\n".join(ensure_script_lines)

        inputs: list[Path] = list(self.srcs)
        if self.requirements and self.requirements.is_file():
            inputs.append(self.requirements)
        if self.pyproject and self.pyproject.is_file():
            inputs.append(self.pyproject)
        # Dep wheel outputs aren't concrete files at Command-construction
        # time (they're a dir), but touching a dep's source invalidates
        # the dep's own stamp, which ripples into the wheel dir's mtime
        # — enough to re-trigger the venv step.
        for w in dep_wheels:
            inputs.append(w.output_path(ctx))

        ensure_cmd = Command.shell_cmd(
            ensure_script,
            cwd=self.project.root,
            label=f"venv {self.name}",
            inputs=tuple(inputs),
            outputs=(key_file,),
        )
        return dep_build_cmds + [
            ensure_cmd,
            self._write_wrapper_cmd(ctx, wrapper, venv_python=self._venv_python(ctx)),
        ]

    def _write_wrapper_cmd(
        self,
        ctx: "BuildContext",
        wrapper: Path,
        venv_python: Path | None,
    ) -> Command:
        """Shell command that writes an executable wrapper at `wrapper`.

        If `venv_python` is None, the wrapper uses the host's python.
        """
        if venv_python is None:
            python_exe = " ".join(
                ctx.toolchain.python.resolved_for(
                    workspace=ctx.workspace_root,
                    project=self.project.root,
                    cwd=self.project.root,
                ).argv
            )
        else:
            python_exe = str(venv_python)

        if ":" in self.entry:
            # "module:function" — run via -c so we don't rely on console_scripts
            module, func = self.entry.split(":", 1)
            invoke = (
                f'exec {python_exe} -c '
                f"'import sys; from {module} import {func} as _f; sys.exit(_f() or 0)' "
                '"$@"'
            )
        else:
            # Script path
            script_path = self.entry
            if not Path(script_path).is_absolute():
                script_path = str((self.project.root / script_path).resolve())
            invoke = f'exec {python_exe} {script_path} "$@"'

        body = "\n".join([
            "#!/usr/bin/env bash",
            "set -e",
            invoke,
            "",
        ])
        script = (
            f"mkdir -p {wrapper.parent} && "
            f"cat > {wrapper} <<'__DEVOPS_WRAPPER_EOF__'\n"
            f"{body}"
            f"__DEVOPS_WRAPPER_EOF__\n"
            f"chmod +x {wrapper}"
        )
        inputs: list[Path] = []
        if self.requirements and self.requirements.is_file():
            inputs.append(self.requirements)
        if self.pyproject and self.pyproject.is_file():
            inputs.append(self.pyproject)
        return Command.shell_cmd(
            script,
            cwd=self.project.root,
            label=f"wrapper {self.name}",
            inputs=tuple(inputs),
            outputs=(wrapper,),
        )

    def describe(self) -> str:
        reqs = str(self.requirements) if self.requirements else "-"
        return (
            f"PythonApp {self.qualified_name}\n"
            f"  entry:        {self.entry}\n"
            f"  pyproject:    {self.pyproject if self.pyproject else '-'}\n"
            f"  requirements: {reqs}\n"
            f"  use_venv:     {self.use_venv}"
        )


# ---------------------------------------------------------------------------
# PythonShiv
# ---------------------------------------------------------------------------


class PythonShiv(Artifact):
    """A self-contained ``.pyz`` zipapp produced by ``shiv``.

    Runs anywhere a compatible Python is installed. Dependencies are
    bundled in the .pyz — no pip/index required at runtime.

    Args:
        name:         target name; output is ``<name>.pyz``
        entry:        ``"module:function"`` entry point fed to ``shiv -e``
        pyproject:    path to the package's pyproject.toml (contains the
                      package sources shiv bundles). Required.
        requirements: extra requirements.txt (e.g. transitive pins). Optional;
                      defaults to ``<project>/requirements.txt`` if present.
        python_shebang: override shebang line in the .pyz (e.g.
                      ``"/usr/bin/env python3.11"``).
    """

    def __init__(
        self,
        name: str,
        entry: str,
        pyproject: str | Path,
        requirements: str | Path | None = None,
        python_shebang: str | None = None,
        python_deps: "list[PythonWheel | str | Ref] | None" = None,
        version: str | None = None,
        deps: dict[str, Target] | None = None,
        doc: str | None = None,
    ) -> None:
        super().__init__(name=name, deps=deps, version=version, doc=doc)
        if ":" not in entry:
            raise ValueError(f"PythonShiv entry must be 'module:function', got {entry!r}")
        self.entry = entry
        self.pyproject = (self.project.root / pyproject).resolve()
        if not self.pyproject.is_file():
            raise FileNotFoundError(f"PythonShiv({name!r}): pyproject.toml not at {self.pyproject}")
        self.requirements: Path | None = (
            (self.project.root / requirements).resolve()
            if requirements is not None
            else _default_requirements(self.project.root)
        )
        self.python_shebang = python_shebang
        self._python_deps_spec: list[PythonWheel | str | Ref] = list(python_deps or [])
        for d in self._python_deps_spec:
            if isinstance(d, PythonWheel):
                self.register_dep(DepKind.PYDEP, d)

    def python_deps(self) -> list["PythonWheel"]:
        return [_resolve_python_dep(d, self.project) for d in self._python_deps_spec]

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / f"{self.name}.pyz"

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        dep_wheels = self.python_deps()
        dep_build_cmds: list[Command] = []
        for w in dep_wheels:
            dep_build_cmds.extend(w.build_cmds(ctx))

        shiv = ctx.toolchain.shiv.resolved_for(
            workspace=ctx.workspace_root,
            project=self.project.root,
            cwd=self.pyproject.parent,
        )
        out = self.output_path(ctx)
        base_args: list[str] = [
            "-o", str(out),
            "-e", self.entry,
            # Pin entry mtimes inside the .pyz so two builds with the
            # same input bytes produce byte-identical zips. shiv has
            # supported this since 1.0.0.
            "--reproducible",
        ]
        if self.requirements and self.requirements.is_file():
            base_args.extend(["-r", str(self.requirements)])
        if self.python_shebang:
            base_args.extend(["-p", self.python_shebang])

        inputs: list[Path] = [self.pyproject]
        if self.requirements and self.requirements.is_file():
            inputs.append(self.requirements)
        for w in dep_wheels:
            inputs.append(w.output_path(ctx))

        # SOURCE_DATE_EPOCH belt-and-suspenders alongside --reproducible:
        # any setuptools/hatchling builds shiv triggers transitively
        # also pin their timestamps to this epoch.
        env = (("SOURCE_DATE_EPOCH", "0"),)

        if dep_wheels:
            # Shell form so we can glob each dep wheel's `dist/*.whl`
            # without knowing versions at config time.
            argv = shiv.invoke(base_args)
            dep_globs = " ".join(f"{w.output_path(ctx)}/*.whl" for w in dep_wheels)
            line = f"{' '.join(argv)} {dep_globs} ."
            cmd = Command.shell_cmd(
                line,
                cwd=self.pyproject.parent,
                env=env,
                label=f"shiv {self.name}",
                inputs=tuple(inputs),
                outputs=(out,),
            )
        else:
            cmd = Command(
                argv=shiv.invoke([*base_args, "."]),
                cwd=self.pyproject.parent,
                env=env,
                label=f"shiv {self.name}",
                inputs=tuple(inputs),
                outputs=(out,),
            )
        return dep_build_cmds + [cmd]

    def describe(self) -> str:
        reqs = str(self.requirements) if self.requirements else "-"
        return (
            f"PythonShiv {self.qualified_name}\n"
            f"  entry:        {self.entry}\n"
            f"  pyproject:    {self.pyproject}\n"
            f"  requirements: {reqs}"
        )
