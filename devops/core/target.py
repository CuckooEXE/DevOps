"""Target, Artifact, Script — the contract the whole system rests on."""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from devops import registry
from devops.version import resolve_version

if TYPE_CHECKING:
    from devops.context import BuildContext
    from devops.core.command import Command


class Project:
    """A directory with a build.py. Targets register themselves against one."""

    def __init__(self, name: str, root: Path):
        self.name = name
        self.root = root

    def __repr__(self) -> str:
        return f"Project({self.name!r}, {self.root})"


class Target(ABC):
    def __init__(
        self,
        name: str,
        deps: dict[str, "Target"] | None = None,
        doc: str | None = None,
        required_tools: tuple[str, ...] | list[str] | None = None,
    ):
        if not name or not isinstance(name, str):
            raise ValueError(f"Target name must be a non-empty string, got {name!r}")
        self.name = name
        self.deps: dict[str, Target] = dict(deps) if deps else {}
        # inspect.cleandoc strips the first line, then dedents the rest by
        # their common leading whitespace — same rules as Python docstrings,
        # so triple-quoted multi-line `doc="""..."""` renders cleanly.
        self.doc: str = inspect.cleandoc(doc) if doc else ""
        # Tools this target needs on PATH that the argv[0] scan can't see
        # (shell commands, pipelines, tools invoked via CustomArtifact).
        # The scan picks up `argv[0]` from every non-shell Command
        # automatically; `required_tools` is the escape hatch.
        self.required_tools: tuple[str, ...] = tuple(required_tools or ())
        self.project: Project = registry.current_project()
        registry.register(self)

    def collect_tool_names(self, ctx: "BuildContext") -> set[str]:
        """Union of declared + auto-detected tool names this target needs.

        Auto-detects ``argv[0]`` from every ``build_cmds`` / ``lint_cmds``
        / ``test_cmds`` Command that isn't shell-form (shell commands hide
        their real executable inside a shell string — users must declare
        those via ``required_tools=``).
        """
        tools: set[str] = set(self.required_tools)

        def _scan(cmds: list["Command"]) -> None:
            for c in cmds:
                if not c.shell and c.argv:
                    tools.add(c.argv[0])

        # Probe each Command list the target may produce. Not every target
        # implements all four; `getattr` with a None default keeps this
        # subtype-agnostic without tripping mypy on attribute access.
        for method_name in ("build_cmds", "lint_cmds", "test_cmds", "run_cmds"):
            method = getattr(self, method_name, None)
            if method is None:
                continue
            try:
                _scan(method(ctx))
            except Exception:
                # Don't let a single target's probe failure hide missing
                # tools in other targets.
                continue
        return tools

    @abstractmethod
    def describe(self) -> str:
        ...

    @property
    def qualified_name(self) -> str:
        return f"{self.project.name}::{self.name}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.qualified_name})"


class Artifact(Target):
    """A target that produces output.

    `arch` is the architecture this artifact is compiled for. Defaults to
    ``"host"``. Targets that are architecture-independent (PythonWheel,
    SphinxDocs) ignore it but it still flows into the output path so
    host-only vs cross-compile trees don't clobber each other.
    """

    def __init__(
        self,
        name: str,
        deps: dict[str, Target] | None = None,
        version: str | None = None,
        doc: str | None = None,
        arch: str = "host",
        extra_inputs: "tuple[str | Path, ...] | list[str | Path] | None" = None,
        required_tools: tuple[str, ...] | list[str] | None = None,
    ):
        super().__init__(name=name, deps=deps, doc=doc, required_tools=required_tools)
        self._version_override = version
        self.arch = arch
        self._extra_inputs: tuple[Path, ...] = self._resolve_extra_inputs(extra_inputs)

    def _resolve_extra_inputs(
        self,
        specs: "tuple[str | Path, ...] | list[str | Path] | None",
    ) -> tuple[Path, ...]:
        """Resolve extra_inputs= relative to the project root.

        These paths are folded into the final Command's `inputs` tuple
        (usually the link/archive step) so changes to linker scripts,
        codegen schemas, or embedded data files invalidate the cache.
        """
        if not specs:
            return ()
        resolved: list[Path] = []
        for s in specs:
            p = Path(s)
            if not p.is_absolute():
                p = (self.project.root / p).resolve()
            resolved.append(p)
        return tuple(resolved)

    @property
    def extra_inputs(self) -> tuple[Path, ...]:
        return self._extra_inputs

    @abstractmethod
    def build_cmds(self, ctx: "BuildContext") -> list["Command"]:
        ...

    def lint_cmds(self, ctx: "BuildContext") -> list["Command"]:
        return []

    def test_cmds(self, ctx: "BuildContext") -> list["Command"]:
        return []

    def clean_cmds(self, ctx: "BuildContext") -> list["Command"]:
        from devops.core.command import Command

        out = self.output_dir(ctx)
        return [Command.shell_cmd(f"rm -rf {out}", label=f"clean {self.name}")]

    @abstractmethod
    def output_path(self, ctx: "BuildContext") -> Path:
        ...

    def output_dir(self, ctx: "BuildContext") -> Path:
        return ctx.project_out(self.project.name, self.name, self.arch)

    def version(self) -> str:
        return resolve_version(self.project.root, self._version_override)


class Script(Target):
    """A target that runs commands but produces no tracked output."""

    def __init__(
        self,
        name: str,
        deps: dict[str, Target] | None = None,
        cmds: list[str] | None = None,
        script: str | Path | None = None,
        doc: str | None = None,
        required_tools: tuple[str, ...] | list[str] | None = None,
    ):
        super().__init__(name=name, deps=deps, doc=doc, required_tools=required_tools)
        if (cmds is None) == (script is None):
            raise ValueError(
                f"Script {name!r} must declare exactly one of cmds=... or script=..."
            )
        self._cmds = list(cmds) if cmds else None
        self._script = Path(script) if script else None

    def describe(self) -> str:
        if self._script is not None:
            return f"Script {self.name} runs {self._script}"
        assert self._cmds is not None  # validated in __init__
        return f"Script {self.name} runs {len(self._cmds)} cmd(s)"

    def run_cmds(self, ctx: "BuildContext") -> list["Command"]:
        from devops.core.command import Command

        if self._script is not None:
            path = self.project.root / self._script if not self._script.is_absolute() else self._script
            return [Command.argv_cmd(["bash", str(path)], cwd=self.project.root, label=f"run {self.name}")]

        assert self._cmds is not None  # validated in __init__
        views = {k: _TargetView(v, ctx) for k, v in self.deps.items()}
        rendered: list[Command] = []
        for line in self._cmds:
            rendered.append(
                Command.shell_cmd(
                    line.format(**views),
                    cwd=self.project.root,
                    label=f"run {self.name}",
                )
            )
        return rendered


class _TargetView:
    """Template-friendly view of a Target for Script cmds expansion.

    Exposes common attributes resolved against a BuildContext so templates
    like '{app.output_path}' work without the user knowing about ctx.
    """

    __slots__ = ("_target", "_ctx")

    def __init__(self, target: "Target", ctx: "BuildContext"):
        self._target = target
        self._ctx = ctx

    def __getattr__(self, attr: str) -> str:
        if attr == "name":
            return self._target.name
        if attr == "qualified_name":
            return self._target.qualified_name
        if attr == "project":
            return self._target.project.name
        if attr == "output_path":
            if isinstance(self._target, Artifact):
                return str(self._target.output_path(self._ctx))
            return ""
        if attr == "output_dir":
            if isinstance(self._target, Artifact):
                return str(self._target.output_dir(self._ctx))
            return ""
        if attr == "version":
            if isinstance(self._target, Artifact):
                return self._target.version()
            return ""
        raise AttributeError(
            f"{type(self._target).__name__} has no template attribute '{attr}'"
        )

    def __str__(self) -> str:
        # Default: "{app}" interpolates to its output_path if any, else name.
        if isinstance(self._target, Artifact):
            return str(self._target.output_path(self._ctx))
        return self._target.name
