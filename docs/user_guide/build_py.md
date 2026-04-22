# Writing a `build.py`

Every project in the workspace has a `build.py` that declares its targets.
The file is plain Python — `devops` imports it at invocation time, and
every `Target(...)` constructor registers itself with the workspace.

## The public API

Everything you need is re-exported from `builder`:

```python
from builder import (
    ElfBinary, ElfSharedObject, StaticLibrary, HeadersOnly,
    PythonWheel, SphinxDocs, Script,
    GoogleTest, Pytest,
    glob, COMMON_C_FLAGS, OptimizationLevel,
)
```

See {doc}`target_types` for the reference of each target type.

## Globbing files

`builder.glob()` returns a list of concrete `Path`s matching one or more
patterns, rooted at the project directory:

```python
srcs=glob(["src/**/*.c", "main.c"], exclude=["src/**/*_test.c"])
```

- Patterns use shell-style wildcards; `**` matches subdirectories
- `exclude=` filters out matches
- `allow_empty=True` permits zero matches (default raises)

Bare strings in `srcs=` are **literal** paths — `srcs="main.c"` is a
single file, not a glob. Use `glob()` when you want expansion.

## Target dependencies

Most dependencies emerge naturally from `libs=` entries:

```python
mylib = ElfSharedObject(name="mylib", srcs=glob("mylib/*.c"))
ElfBinary(name="app", srcs=glob("app.c"), libs=[mylib])
```

The `libs=[mylib]` both links against `libmylib.so` and registers `mylib`
as a build-time dependency, so `devops build app` builds `mylib` first.

For targets that need to depend on something but don't fit `libs=` (e.g.
a `Script` that needs an artifact built before it runs), use `deps`:

```python
Script(
    name="deploy",
    deps={"app": app, "lib": mylib},
    cmds=[
        "scp {app.output_path} {lib.output_path} prod:/usr/local/bin/",
    ],
)
```

Keys in `deps=` become template variables in inline `cmds=`. See
{doc}`scripts` for the full template grammar.

## Flags, defines, includes

C-family targets accept:

```python
ElfBinary(
    name="app",
    srcs=glob("src/*.c"),
    includes=["include"],                    # -I./include
    flags=COMMON_C_FLAGS + ("-Wall",),       # appended to profile flags
    defs={"FOO": None, "BAR": "1"},          # -DFOO  -DBAR=1
    undefs=["QUX"],                          # -UQUX
)
```

The build profile (`--profile Debug|Release|ReleaseSafe`) supplies its
own flags (`-O0 -ggdb -DDEBUG` etc.) — your `flags=` list is appended on
top. Nothing to restate per-profile.

## Subclassing for team defaults

If most of your binaries share a lot of flags, bake them in with a
subclass once:

```python
class TeamBinary(ElfBinary):
    """ElfBinary with the team's mandatory hardening + warnings."""

    def __init__(self, **kwargs):
        baked = tuple(COMMON_C_FLAGS) + ("-Werror", "-fstack-protector-strong")
        user = tuple(kwargs.pop("flags", ()) or ())
        super().__init__(flags=baked + user, **kwargs)
```

Then each binary becomes:

```python
TeamBinary(name="api", srcs=glob("api/*.c"), libs=[libcommon])
```

and inherits the team flags automatically. See
{doc}`../developer_guide/adding_a_target_type` for adding a fully new
target type (not just a subclass).

## Documenting a target

Every target accepts a `doc="..."` kwarg. It's shown under
`devops describe`, and Python-native (triple-quoted, indented text is
dedented on your behalf via `inspect.cleandoc`):

```python
ElfBinary(
    name="api",
    srcs=glob("api/*.c"),
    doc="""The public-facing HTTP API.

        Owned by the platform team. If the `/healthz` probe ever
        goes red, check its logs first.""",
)
```
