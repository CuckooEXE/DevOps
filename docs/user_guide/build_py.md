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

`includes=` also accepts a `HeadersOnly` target (or a `Ref` resolving to
one) — the binary picks up `-I<staged include dir>` and the header
target flows into `deps` so topo-sort builds it first:

```python
hdrs = HeadersOnly(name="PublicHeaders", srcs=glob("include/*.h"))

ElfBinary(
    name="app",
    srcs=glob("src/*.c"),
    includes=[hdrs, "third_party/foo/include"],   # target + bare dir
)
```

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

## Extra cache inputs

For C-family (and Zig) targets, `devops` tracks headers automatically via
`-MMD` depfiles — change a `#include`d header and the cache invalidates
the compiles that depend on it. No declaration needed.

For non-header inputs that the compiler can't see (linker scripts,
codegen schemas, embedded data files), declare them with
`extra_inputs=`:

```python
ElfBinary(
    name="embedded",
    srcs=glob("src/*.c"),
    extra_inputs=["linker.ld", "fw_config.toml"],
)
```

`extra_inputs` paths are folded into the final Command's input set (the
link step for binaries/shared objects, the `ar` step for static libs),
so touching any of them invalidates the final artifact — without
forcing every compile step to re-run.

## Declaring tool dependencies

`devops doctor` discovers most tool dependencies automatically — it
scans every Command's `argv[0]` and checks it's on PATH. But shell
commands (used by `Script` and `CustomArtifact`) hide their real
executables inside a shell string, so the scan can't see them.

Declare them explicitly via `required_tools=`:

```python
CustomArtifact(
    name="stripped",
    inputs={"bin": myApp},
    outputs=["app_stripped"],
    cmds=["strip --strip-all {bin.output_path} -o {out[0]}"],
    required_tools=["strip"],
)

Script(
    name="deploy",
    deps={"app": myApp},
    cmds=["rsync -az {app.output_path} prod:/opt/"],
    required_tools=["rsync"],
)
```

`devops doctor` then lists these under the target's name and reports
them as missing if absent. Run in CI before `devops build` so missing
tools fail fast with a full list instead of one at a time mid-build.

## Cross-project Python dependencies

For `PythonApp` and `PythonShiv`, declare other `PythonWheel` targets
as `python_deps=`:

```python
# Monorepo — same workspace
shared_wheel = PythonWheel(name="shared", pyproject="shared/pyproject.toml")

PythonApp(
    name="app",
    entry="app.cli:main",
    pyproject="app/pyproject.toml",
    python_deps=[shared_wheel],       # Target instance
)

# Cross-repo — typed remote reference (see also: Remote references)
from builder import GitRef

PythonShiv(
    name="app",
    entry="app.cli:main",
    pyproject="app/pyproject.toml",
    python_deps=[
        "::shared",                                          # local "::name"
        GitRef("ssh://git@github.com/acme/libfoo", target="libfoo"),
    ],
)
```

At build time, `devops` builds each dep's wheel first (if not cached),
then feeds the `dist/*.whl` into the app's venv (`pip install`) or the
shiv `.pyz` (positional arg). `Target`-instance deps flow into
`self.deps` for topo-sort; `"::name"` and remote `Ref` entries resolve
lazily at build time so no network traffic happens at `build.py`
import.

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
