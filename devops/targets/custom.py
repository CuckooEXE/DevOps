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
from devops.targets._specs import inline_ref_build_cmds, resolve_target_spec

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
        # Each value resolved later: Target/Ref → Target view, Path → str.
        self._inputs_raw: dict[str, "Target | Ref | Path"] = {}
        if inputs:
            for k, v in inputs.items():
                if isinstance(v, Target):
                    self._inputs_raw[k] = v
                    # Suffix on the input key, not the target name, so two
                    # inputs on the same Target stay distinguishable.
                    self.register_dep(DepKind.INPUT, v, suffix=k)
                elif isinstance(v, Ref):
                    self._inputs_raw[k] = v
                elif isinstance(v, (str, Path)):
                    p = Path(v)
                    if not p.is_absolute():
                        p = (self.project.root / p).resolve()
                    self._inputs_raw[k] = p
                else:
                    raise TypeError(
                        f"CustomArtifact({name!r}): input {k!r} must be "
                        f"Target, Ref, str, or Path; got {type(v).__name__}"
                    )

    def output_path(self, ctx: "BuildContext") -> Path:
        return self.output_dir(ctx) / self.outputs_rel[0]

    def output_paths(self, ctx: "BuildContext") -> list[Path]:
        return [self.output_dir(ctx) / o for o in self.outputs_rel]

    def inputs_for(self, ctx: "BuildContext") -> dict[str, Path | Target]:
        return dict(self._inputs_raw)

    def build_cmds(self, ctx: "BuildContext") -> list[Command]:
        prelude = inline_ref_build_cmds(
            [v for v in self._inputs_raw.values() if isinstance(v, Ref)],
            ctx,
        )
        bindings: dict[str, object] = {}
        input_paths: list[Path] = []
        for key, val in self._inputs_raw.items():
            if isinstance(val, Ref):
                # Resolve at build_cmds time so the network/cache fetch
                # only fires when the input is actually needed.
                target = resolve_target_spec(
                    val, kwarg=f"inputs[{key!r}]",
                    ident=f"CustomArtifact({self.name!r})",
                )
                bindings[key] = _TargetView(target, ctx)
                if isinstance(target, Artifact):
                    input_paths.append(target.output_path(ctx))
            elif isinstance(val, Target):
                bindings[key] = _TargetView(val, ctx)
                if isinstance(val, Artifact):
                    input_paths.append(val.output_path(ctx))
            else:
                bindings[key] = str(val)
                input_paths.append(val)

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
            *prelude,
            Command.shell_cmd(
                joined,
                cwd=self.project.root,
                label=self.name,
                inputs=(*input_paths, *self._extra_inputs),
                outputs=tuple(out_paths),
            ),
        ]

    def describe(self) -> str:
        def _fmt(v: "Target | Ref | Path") -> str:
            if isinstance(v, Target):
                return v.qualified_name
            if isinstance(v, Ref):
                return v.to_spec()
            return str(v)

        ins = ", ".join(f"{k}={_fmt(v)}" for k, v in self._inputs_raw.items()) or "-"
        return (
            f"CustomArtifact {self.qualified_name} ({self.arch})\n"
            f"  inputs:  {ins}\n"
            f"  outputs: {', '.join(self.outputs_rel)}\n"
            f"  cmds:    {len(self.cmd_templates)} step(s)"
        )
