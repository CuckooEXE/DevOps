"""Install target — stages a built artifact outside the build tree.

Dispatches per artifact type:
    ElfBinary / ElfSharedObject / StaticLibrary → install -m <mode> -D <src> <dest>/<filename>
    HeadersOnly                                  → copy the staged include/ tree under <dest>
    PythonWheel                                  → pip install <wheel>
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from devops.core.command import Command
from devops.core.target import Artifact, Target
from devops.remote import Ref
from devops.targets._paths import validate_octal_mode
from devops.targets._specs import resolve_target_spec

if TYPE_CHECKING:
    from devops.context import BuildContext


class Install(Target):
    """Install an Artifact at `dest` (or, for a PythonWheel, into pip).

    The Install itself doesn't produce tracked output — it's more like a
    Script in that respect — but its command shape depends on what it's
    installing, which is why it's its own Target type rather than a bare
    Script.

    Args:
        name:     unique target name
        artifact: the Artifact to install (must already be registered),
                  or a Ref pointing at one in a remote project. For a
                  Ref the type-based dispatch (PythonWheel / ElfBinary /
                  HeadersOnly / etc.) is deferred to ``install_cmds``.
        dest:     destination directory (ignored for PythonWheel). Required
                  unless ``artifact`` is a PythonWheel; for a Ref we can't
                  know the type yet, so dest is checked at install time.
        mode:     file mode for `install -m` on binaries/libs. Default "0755".
        sudo:     prefix `sudo ` on install commands (pip is run as-is)
        pip_args: extra arguments passed to `pip install` for PythonWheels.
                  Default ("--user",). Pass ("--break-system-packages",)
                  on systems that enforce PEP 668, or () for in-venv use.
        doc:      freeform description shown by `devops describe`
    """

    def __init__(
        self,
        name: str,
        artifact: "Artifact | Ref",
        dest: str | Path | None = None,
        mode: str = "0755",
        sudo: bool = False,
        pip_args: tuple[str, ...] = ("--user",),
        doc: str | None = None,
    ) -> None:
        from devops.targets.python import PythonWheel

        validate_octal_mode(mode, "mode", f"Install({name!r})")

        if isinstance(artifact, Artifact):
            if not isinstance(artifact, PythonWheel) and dest is None:
                raise ValueError(
                    f"Install({name!r}): dest= required for "
                    f"{type(artifact).__name__}"
                )
            deps = {f"_install_{artifact.name}": artifact}
        elif isinstance(artifact, Ref):
            # Type-based dest validation deferred — we resolve at install_cmds.
            deps = {}
        else:
            raise TypeError(
                f"Install.artifact must be Artifact or Ref, "
                f"got {type(artifact).__name__}"
            )

        super().__init__(name=name, deps=deps, doc=doc)
        self._artifact_spec: "Artifact | Ref" = artifact
        self.dest: Path | None = Path(dest) if dest is not None else None
        self.mode = mode
        self.sudo = sudo
        self.pip_args = tuple(pip_args)

    @property
    def artifact(self) -> Artifact:
        """Eagerly-bound artifact when one was passed; otherwise raises.

        For a Ref-typed Install, use ``_resolve_artifact`` from
        install_cmds to defer the (possibly-network-backed) resolution.
        """
        if isinstance(self._artifact_spec, Artifact):
            return self._artifact_spec
        raise RuntimeError(
            f"Install({self.name!r}): artifact is a Ref; resolve via "
            f"install_cmds (BuildContext required)"
        )

    def _resolve_artifact(self) -> Artifact:
        target = resolve_target_spec(
            self._artifact_spec,
            kwarg="artifact", ident=f"Install({self.name!r})",
        )
        if not isinstance(target, Artifact):
            raise TypeError(
                f"Install({self.name!r}): artifact resolved to "
                f"{type(target).__name__}, expected an Artifact"
            )
        return target

    def describe(self) -> str:
        dest_str = str(self.dest) if self.dest else "(pip)"
        if isinstance(self._artifact_spec, Artifact):
            artifact_str = self._artifact_spec.qualified_name
        else:
            artifact_str = self._artifact_spec.to_spec()
        return (
            f"Install {self.qualified_name}\n"
            f"  artifact: {artifact_str}\n"
            f"  dest:     {dest_str}\n"
            f"  mode:     {self.mode}"
        )

    def install_cmds(self, ctx: "BuildContext") -> list[Command]:
        from devops.targets.c_cpp import (
            ElfBinary,
            ElfSharedObject,
            HeadersOnly,
            StaticLibrary,
        )
        from devops.targets.python import PythonWheel

        a = self._resolve_artifact()
        src = a.output_path(ctx)

        if isinstance(a, PythonWheel):
            return self._pip_install_cmds(ctx, src)

        if self.dest is None:
            raise ValueError(
                f"Install({self.name!r}): dest= required for "
                f"{type(a).__name__}"
            )

        if isinstance(a, ElfSharedObject):
            filename = f"lib{a.name}.so"
        elif isinstance(a, StaticLibrary):
            filename = f"lib{a.name}.a"
        elif isinstance(a, ElfBinary):
            filename = a.name
        elif isinstance(a, HeadersOnly):
            return self._install_headers_cmds(src)
        else:
            raise TypeError(
                f"Install doesn't know how to place a {type(a).__name__}; "
                f"add handling to install.py if you want to support it."
            )

        target_path = self.dest / filename
        argv: tuple[str, ...] = (
            "install",
            "-m", self.mode,
            "-D",
            str(src),
            str(target_path),
        )
        if self.sudo:
            argv = ("sudo", *argv)
        return [
            Command(
                argv=argv,
                cwd=self.project.root,
                label=f"install {filename} -> {target_path}",
                inputs=(src,),
            )
        ]

    def _install_headers_cmds(self, src_dir: Path) -> list[Command]:
        assert self.dest is not None
        prefix = ["sudo"] if self.sudo else []
        # Use a portable cp -a <src>/. <dest>/ pattern: contents of src_dir
        # get copied under dest; dest created if absent.
        return [
            Command(
                argv=tuple(prefix + ["mkdir", "-p", str(self.dest)]),
                cwd=self.project.root,
                label=f"mkdir {self.dest}",
            ),
            Command(
                argv=tuple(prefix + ["cp", "-a", f"{src_dir}/.", f"{self.dest}/"]),
                cwd=self.project.root,
                label=f"install headers -> {self.dest}",
                inputs=(src_dir,),
            ),
        ]

    def _pip_install_cmds(self, ctx: "BuildContext", wheel_dir: Path) -> list[Command]:
        # The wheel filename is built by `python -m build`; we don't know
        # the exact version at config time, so glob at install time via
        # a shell-form command. Keeps the Command portable through a
        # Docker-wrapped python if the user has configured one.
        python = ctx.toolchain.python.resolved_for(
            workspace=ctx.workspace_root,
            project=self.project.root,
            cwd=self.project.root,
        )
        pip_invoke = python.invoke(["-m", "pip", "install", *self.pip_args])
        # shell-form so the glob is expanded at run time
        line = f"{' '.join(pip_invoke)} {wheel_dir}/*.whl"
        return [
            Command.shell_cmd(
                line,
                cwd=self.project.root,
                label=f"pip install {self.artifact.name}",
            )
        ]
