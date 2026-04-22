# Adding a new tool (linter / formatter / checker)

A tool in this codebase is a thin wrapper that turns a target into one
or more `Command` objects. Linters (clang-tidy, cppcheck, black, ruff)
all live in `devops/tools/`.

## The pattern

Each tool module exports a function that takes a target + a
`BuildContext` and returns a `list[Command]`:

```python
# devops/tools/clang.py
def lint_for_ccompile(target: "CCompile", ctx: "BuildContext") -> list[Command]:
    compile_flags = target._compile_flags(ctx)   # reuse the flag vector
    project_root = target.project.root
    cmds: list[Command] = []

    tidy = ctx.toolchain.clang_tidy.resolved_for(
        workspace=ctx.workspace_root,
        project=project_root,
        cwd=project_root,
    )
    for src in target.srcs:
        cmds.append(
            Command(
                argv=tidy.invoke([str(src), "--", *compile_flags]),
                cwd=project_root,
                label=f"clang-tidy {src.name}",
                inputs=(src,),
            )
        )
    return cmds
```

The target's `lint_cmds()` delegates to this function:

```python
# devops/targets/c_cpp.py
class ElfBinary(CCompile, Artifact):
    def lint_cmds(self, ctx):
        from devops.tools import clang
        return clang.lint_for_ccompile(self, ctx)
```

## Adding a new linter

Say you want to add `iwyu` (include-what-you-use) as an additional
C/C++ linter.

### 1. Add the tool to the `Toolchain`

`devops/context.py`:

```python
@dataclass
class Toolchain:
    ...
    iwyu: Tool = field(default_factory=lambda: Tool.of("include-what-you-use"))
```

### 2. Write the wrapper

`devops/tools/iwyu.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

from devops.core.command import Command

if TYPE_CHECKING:
    from devops.context import BuildContext
    from devops.targets.c_cpp import CCompile


def check_for_ccompile(target: "CCompile", ctx: "BuildContext") -> list[Command]:
    compile_flags = target._compile_flags(ctx)
    project_root = target.project.root
    iwyu = ctx.toolchain.iwyu.resolved_for(
        workspace=ctx.workspace_root,
        project=project_root,
        cwd=project_root,
    )
    return [
        Command(
            argv=iwyu.invoke([str(s), "--", *compile_flags]),
            cwd=project_root,
            label=f"iwyu {s.name}",
            inputs=(s,),
        )
        for s in target.srcs
    ]
```

### 3. Wire into `lint_cmds()`

Extend `ElfBinary.lint_cmds()` (or make it an opt-in kwarg). Simplest:

```python
def lint_cmds(self, ctx):
    from devops.tools import clang, iwyu
    return clang.lint_for_ccompile(self, ctx) + iwyu.check_for_ccompile(self, ctx)
```

### 4. Add tests

Cover both the Command shape and the "tool missing surfaces as a
ToolMissing error rather than a crash" path. See
`tests/test_runner_cache.py::test_tool_missing_raises_typed_error` for
the pattern.

## Tips

- **Always pass through `target._compile_flags(ctx)`.** That's the
  single source of truth for flags. Every custom flag, every profile,
  every define lives there.
- **Use `Tool.resolved_for(...)`**, never `subprocess` directly. That's
  what keeps Docker-wrapped toolchains working.
- **Don't `capture_output=True` for lint tools by default.** Most users
  want the warnings to surface. If you need quiet-on-success, prefer
  the tool's own `-Q` / `-q` flag.
- **Return Commands, don't execute.** The CLI layer owns execution,
  dry-run, caching, and failure aggregation.
