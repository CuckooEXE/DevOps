"""Target, Artifact, Script — the contract the whole system rests on."""

from __future__ import annotations

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
    def __init__(self, name: str, deps: dict[str, "Target"] | None = None):
        if not name or not isinstance(name, str):
            raise ValueError(f"Target name must be a non-empty string, got {name!r}")
        self.name = name
        self.deps: dict[str, Target] = dict(deps) if deps else {}
        self.project: Project = registry.current_project()
        registry.register(self)

    @abstractmethod
    def describe(self) -> str:
        ...

    @property
    def qualified_name(self) -> str:
        return f"{self.project.name}::{self.name}"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.qualified_name})"


class Artifact(Target):
    """A target that produces output."""

    def __init__(
        self,
        name: str,
        deps: dict[str, Target] | None = None,
        version: str | None = None,
    ):
        super().__init__(name=name, deps=deps)
        self._version_override = version

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
        return ctx.project_out(self.project.name, self.name)

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
    ):
        super().__init__(name=name, deps=deps)
        if (cmds is None) == (script is None):
            raise ValueError(
                f"Script {name!r} must declare exactly one of cmds=... or script=..."
            )
        self._cmds = list(cmds) if cmds else None
        self._script = Path(script) if script else None

    def describe(self) -> str:
        if self._script:
            return f"Script {self.name} runs {self._script}"
        return f"Script {self.name} runs {len(self._cmds)} cmd(s)"

    def run_cmds(self, ctx: "BuildContext") -> list["Command"]:
        from devops.core.command import Command

        if self._script:
            path = self.project.root / self._script if not self._script.is_absolute() else self._script
            return [Command.argv_cmd(["bash", str(path)], cwd=self.project.root, label=f"run {self.name}")]

        views = {k: _TargetView(v, ctx) for k, v in self.deps.items()}
        rendered = []
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
