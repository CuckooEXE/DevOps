"""CustomArtifact — generic "run these commands, produce these files" target.

For post-processing (strip / objcopy / upx), codegen, any arbitrary tool a
user wants to plug into the build graph. The goal is: if you can write it
as a shell command that reads some files and writes some files,
CustomArtifact makes it a cacheable, topo-ordered target without writing
a new subclass.

Template grammar (Python ``str.format``):

    inputs    — dict; each key becomes a top-level template name. The
                value is a ``_TargetView`` when the input is a Target, or
                a plain absolute-path string when the input is a file
                path (str/Path). Example: ``{src.output_path}``,
                ``{schema}``.
    out       — a list bound under the name ``out``. Access individual
                outputs as ``{out[0]}``, ``{out[1]}``, …

Everything the target produces lives under its ``output_dir``; ``outputs=``
entries are filenames relative to that directory.

Single shell invocation: all ``cmds`` are joined under ``set -e`` into one
shell command so the whole step is atomic in the cache — a failure
partway through doesn't leave a stamp behind pointing at a half-built
primary output.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from devops.core.command import Command
from devops.core.target import Artifact, DepKind, Target, _TargetView
from devops.remote import Ref
from devops.targets._specs import ResolvedSource, coerce_source, ref_prelude_for

if TYPE_CHECKING:
    from devops.context import BuildContext


class CustomArtifact(Artifact):
    def __init__(
        self,
        name: str,
        outputs: list[str],
        cmds: list[str],
        inputs: "dict[str, Target | Ref | str | Path] | None" = None,
        version: str | None = None,
        deps: dict[str, Target] | None = None,
        doc: str | None = None,
        arch: str = "host",
        extra_inputs: "tuple[str | Path, ...] | list[str | Path] | None" = None,
        required_tools: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        super().__init__(
            name=name, deps=deps, version=version, doc=doc, arch=arch,
            extra_inputs=extra_inputs, required_tools=required_tools,
        )
        if not outputs:
            raise ValueError(f"CustomArtifact({name!r}): outputs= must have at least one entry")
        if not cmds:
            raise ValueError(f"CustomArtifact({name!r}): cmds= must have at least one entry")

        self.outputs_rel: list[str] = list(outputs)
        self.cmd_templates: list[str] = list(cmds)
        # Each input parsed once at config time; resolved per-build via
        # ResolvedSource.resolve / .resolve_target. Suffix on the input
        # key, not the target name, so two inputs on the same Target
        # stay distinguishable in the deps dict.
        self._inputs: dict[str, ResolvedSource] = {}
        if inputs:
            for k, v in inputs.items():
                self._inputs[k] = coerce_source(
                    v, kwarg=f"inputs[{k!r}]",
                    ident=f"CustomArtifact({name!r})",
                    project_root=self.project.root,
                    deps=self.deps, dep_kind=DepKind.INPUT, dep_suffix=k,
                )

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / self.outputs_rel[0]

    def output_paths(self, ctx: "BuildContext") -> list[Path]:
        return [self.output_dir(ctx) / o for o in self.outputs_rel]

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        bindings: dict[str, object] = {}
        input_paths: list[Path] = []
        for key, source in self._inputs.items():
            tgt = source.resolve_target()
            if tgt is None:
                # Path source: bind the absolute path string for templates.
                assert source.path is not None
                bindings[key] = str(source.path)
                input_paths.append(source.path)
            else:
                # Target/Ref: bind a _TargetView so templates can read
                # {key.output_path}, {key.name}, etc.
                bindings[key] = _TargetView(tgt, ctx)
                if isinstance(tgt, Artifact):
                    input_paths.append(tgt.output_path(ctx))

        out_paths = self.output_paths(ctx)
        bindings["out"] = [str(p) for p in out_paths]

        rendered: list[str] = []
        for i, tmpl in enumerate(self.cmd_templates):
            try:
                rendered.append(tmpl.format(**bindings))
            except KeyError as e:
                raise KeyError(
                    f"CustomArtifact({self.name!r}) cmds[{i}] references unknown name {e}; "
                    f"known: {sorted(bindings)}"
                ) from None

        # Make sure the primary output's parent exists before user commands run
        mkdir = f"mkdir -p {out_paths[0].parent}"
        joined = "\n".join(["set -e", mkdir, *rendered])

        return [
            *ref_prelude_for(self._inputs.values(), ctx),
            Command.shell_cmd(
                joined,
                cwd=self.project.root,
                label=self.name,
                inputs=(*input_paths, *self._extra_inputs),
                outputs=tuple(out_paths),
            ),
        ]

    def describe(self) -> str:
        ins = ", ".join(
            f"{k}={s.describe_str()}" for k, s in self._inputs.items()
        ) or "-"
        return (
            f"CustomArtifact {self.qualified_name} ({self.arch})\n"
            f"  inputs:  {ins}\n"
            f"  outputs: {', '.join(self.outputs_rel)}\n"
            f"  cmds:    {len(self.cmd_templates)} step(s)"
        )
