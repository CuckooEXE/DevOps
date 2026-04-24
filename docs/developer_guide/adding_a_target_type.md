# Adding a new target type

Two flavours: subclass an existing target to bake in defaults, or land a
wholly new target type.

## Subclass in-tree vs ship a plugin

**Subclass in-tree** when the target is internal to your org and you
want it to live alongside the rest of the devops source. Cheap to
write; no packaging overhead. The `TeamBinary` example below is the
canonical shape.

**Write a plugin** when the target is meant to be reusable across
repos or shared with other teams. A plugin is a normal
pip-installable package that registers its classes via the
`devops.targets` entry-point group; consumers then write
`from builder.plugins import YourTarget`. See {doc}`writing_a_plugin`
for the full reference and `plugins/devops-example-tarball/` in the
repo for runnable scaffolding.

The mechanics below apply to in-tree additions; the plugin path is
almost identical but imports come from `devops.api` and tool
registration goes through `DEFAULT_TOOLCHAIN_EXTRAS` instead of
`Toolchain` dataclass fields.

## Subclassing an existing target

If most of your team's binaries share flags, pin them once:

```python
from builder import ElfBinary, COMMON_C_FLAGS


class TeamBinary(ElfBinary):
    """ElfBinary with the team's `-Werror` policy."""

    def __init__(self, **kwargs):
        baked = tuple(COMMON_C_FLAGS) + ("-Werror",)
        user = tuple(kwargs.pop("flags", ()) or ())
        super().__init__(flags=baked + user, **kwargs)
```

Drop it in a shared `myteam/builder.py`, import from there, and every
binary in the org picks up the policy.

## Writing a wholly new target type

Suppose you want to add a `GoBinary` target that wraps `go build`.
Create `devops/targets/go.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from devops.core.command import Command
from devops.core.target import Artifact, Target
from devops.targets.c_cpp import SourcesSpec, _resolve_sources

if TYPE_CHECKING:
    from devops.context import BuildContext


class GoBinary(Artifact):
    def __init__(
        self,
        name: str,
        srcs: SourcesSpec,
        package: str = ".",
        version: str | None = None,
        deps: dict[str, Target] | None = None,
        doc: str | None = None,
    ) -> None:
        super().__init__(name=name, deps=deps, version=version, doc=doc)
        self.srcs = _resolve_sources(self.project.root, srcs)
        self.package = package

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / self.name

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        # For an in-tree target, add a new field to Toolchain in
        # devops/context.py (`go: Tool = field(default_factory=...)`)
        # and read it as `ctx.toolchain.go`. For a plugin, register
        # the tool into `ctx.toolchain.extras["go"]` via the plugin's
        # register() hook instead.
        tool = ctx.toolchain.go.resolved_for(
            workspace=ctx.workspace_root,
            project=self.project.root,
            cwd=self.project.root,
        )
        out = self.output_path(ctx)
        return [
            Command(
                argv=tool.invoke(["build", "-o", str(out), self.package]),
                cwd=self.project.root,
                label=f"go build {self.name}",
                inputs=tuple(self.srcs),
                outputs=(out,),
            )
        ]

    def describe(self) -> str:
        return (
            f"GoBinary {self.qualified_name}\n"
            f"  package: {self.package}\n"
            f"  srcs:    {len(self.srcs)} file(s)"
        )
```

Then re-export from `builder/__init__.py`:

```python
from devops.targets.go import GoBinary
```

and add to `__all__`.

## Checklist

- [ ] Subclass `Artifact` (or `Script` if it doesn't produce output)
- [ ] Implement `output_path(ctx)` — where the build result lands
- [ ] Implement `build_cmds(ctx)` — a list of `Command`s
- [ ] Optionally override `lint_cmds(ctx)` / `test_cmds(ctx)` /
      `clean_cmds(ctx)`
- [ ] Implement `describe()` → short pretty header string
- [ ] Add a new `Tool` to `devops/context.Toolchain` if you need a new
      executable, with a sensible default argv prefix (in-tree) — or
      register it via `DEFAULT_TOOLCHAIN_EXTRAS` from a plugin's
      `register()` hook so `ctx.toolchain.extras["<name>"]` resolves
- [ ] Re-export from `builder/__init__.py` and list in `__all__`
      (in-tree only — plugin classes auto-inject into `builder.plugins`)
- [ ] Add tests covering the command shape (see the `test_targets_*.py`
      files for the pattern)

## Inheriting shared machinery

If your target is C-family, inherit from `CCompile` (mixin) in addition
to `Artifact`. You get `_compile_flags(ctx)`, `_compile_command(...)`,
`_link_flags_for_libs(ctx)` for free, and `lint_cmds()` automatically
reuses the exact flag vector — so adding a new C-family target gets
lint support automatically via `lint_for_ccompile(self, ctx)`.
