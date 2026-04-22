# Target types reference

Every target type produces either an **Artifact** (something on disk) or a
**Script** (something that runs). All targets share these kwargs:

- `name` (required) — unique within its project
- `deps` — dict of named dependencies (used for Scripts + topological order)
- `doc` — freeform description shown by `devops describe`

## ElfBinary

Compiles an executable.

```python
ElfBinary(
    name="app",
    srcs=glob("src/*.c"),      # + main.c, etc.
    includes=["include"],       # -I paths
    flags=("-Wall", "-Wextra"),
    defs={"DEBUG": None},       # -DDEBUG
    undefs=["NDEBUG"],
    libs=[mylib, "ssl"],        # Targets or -l<name> for system libs
    is_cxx=False,               # True to use clang++ instead of clang
    tests={"srcs": glob("tests/*.cc")},  # sugar: see below
    version="1.2.3",            # optional override; falls back to git describe
)
```

The `tests={...}` sugar creates a sibling `GoogleTest(name="<name>Tests",
target=self, **kwargs)` automatically.

Output: `build/<profile>/<project>/<name>/<name>`

## ElfSharedObject

Same as `ElfBinary`, but produces `lib<name>.so` with `-fPIC` and
`-shared` added. Linkable from other C/C++ targets via `libs=[this]`.

## StaticLibrary

Compiles sources and archives via `ar rcs` into `lib<name>.a`. No linking
step, no `libs=`.

## HeadersOnly

A bundle of headers for downstream targets to pick up as includes:

```python
HeadersOnly(name="common-headers", srcs=glob("include/**/*.h"))
```

The build stages headers into `build/<profile>/<project>/<name>/include/`.

## CObjectFile + LdBinary

Split compile and link into two phases — useful for freestanding /
embedded / bootloader-style builds where you control every linker flag,
or for producing a relocatable `.o` via `ld -r`.

```python
objs = CObjectFile(
    name="app_objs",
    srcs=glob("src/*.c"),
    includes=["include"],
    flags=COMMON_C_FLAGS,
    pic=False,               # set True for -fPIC
)

LdBinary(
    name="app",
    objs=[objs, "libfoo.a"],      # CObjectFile targets, archive paths, or literal flags
    linker_script="layout.ld",    # -T <script>; flows into cache inputs
    map_file="app.map",           # -Map <path>; declared as an output
    entry="_start",               # -e <symbol>
    extra_ld_flags=("-nostdlib",),
)
```

- `CObjectFile.output_path(ctx)` is the obj dir; `.object_files(ctx)`
  returns the exact `.o` list
- `LdBinary` invokes `ctx.toolchain.ld` directly (not `cc`)
- Linker script edits invalidate the ld step's cache via `Command.inputs`
- For normal userspace builds prefer `ElfBinary` — it drives cc, which
  handles libc startup, default search paths, rpath, etc.

## CustomArtifact

Run arbitrary shell pipelines as a first-class, cacheable target. Use
for post-processing (`strip`, `objcopy`, `upx`), codegen, or any tool
that doesn't fit an existing target type.

```python
stripped = CustomArtifact(
    name="app_stripped",
    inputs={"bin": myApp},            # Target or Path refs
    outputs=["app.stripped"],         # filenames under output_dir
    cmds=[
        "cp {bin.output_path} {out[0]}",
        "strip --strip-all {out[0]}",
    ],
    required_tools=["cp", "strip"],   # for `devops doctor`
)
```

- `inputs=` values can be `Target` (bound as a template view) or
  `str`/`Path` (bound as absolute-path strings); Target values flow
  into `self.deps` for topo-sort
- `outputs=` is a list; reference via `{out[0]}`, `{out[1]}`, …
- Every `cmds` entry is joined under `set -e` into one shell
  invocation — atomic caching, clean failure mid-pipeline
- Unknown template names raise `KeyError` at build time with the known
  set listed

See also {doc}`cross_compile` — each input's `.output_path` and
`.output_dir` correctly reflect the arch the artifact was built for.

## PythonWheel

Builds a wheel via `python -m build --wheel`:

```python
PythonWheel(
    name="mypkg",
    pyproject="subdir/pyproject.toml",   # or just "pyproject.toml"
    srcs=glob("subdir/mypkg/**/*.py"),
    tests={"srcs": glob("subdir/tests/test_*.py")},
)
```

Runs `python -m build` from the directory containing `pyproject.toml`, so
relative imports resolve correctly. The `tests=` sugar desugars to a
`Pytest(name="<name>Tests", target=self)` that pre-pends the wheel's
source directory to `PYTHONPATH`.

## SphinxDocs

Runs `sphinx-build -b html <conf> <out>`:

```python
SphinxDocs(name="docs", srcs=glob("docs/**/*"), conf="docs")
```

`lint_cmds()` runs `sphinx-build -Q -W -n` (quiet, warnings-as-errors,
nitpicky) — silent on pass, loud on warning.

## Script

Runs commands but produces no tracked output:

```python
Script(name="run-ci", cmds=["pytest -q", "ruff check ."])
Script(name="bash-form", script="scripts/deploy.sh")

Script(
    name="push-and-run",
    deps={"app": myCoolApp, "lib": myLib},
    cmds=[
        "scp {app.output_path} {lib.output_path} host:/tmp/",
        "ssh host LD_LIBRARY_PATH=/tmp /tmp/{app.name}",
    ],
)
```

Exactly one of `cmds=` or `script=` must be given. See {doc}`scripts` for
the templating grammar in `cmds=`.

## GoogleTest

Compiles a C++ test binary that inherits its target's compile environment:

```python
GoogleTest(
    name="mylibTests",
    srcs=glob("tests/*.cc"),
    target=mylib,               # must be a CCompile artifact
    extra_flags=("-fsanitize=address",),   # optional
    extra_libs=("gtest", "gtest_main", "pthread"),  # default
)
```

- Inherits `flags`, `includes`, `defs`, `undefs` from `target`
- If `target` is a library, links against it directly
- If `target` is an `ElfBinary`, links everything the binary itself
  links (so tests see the same library env)
- Always runs as C++ (`is_cxx=True`), linking `-lgtest -lgtest_main
  -lpthread` by default

## Pytest

Runs pytest against source files, optionally tied to a `PythonWheel`:

```python
Pytest(name="t", srcs=glob("tests/*.py"), target=mypkg)
```

When `target=` is set, prepends the wheel's source dir to `PYTHONPATH`
so `from mypkg import ...` works without installing first.

## ZigBinary

Delegates to `zig build` against a project's `build.zig`:

```python
ZigBinary(
    name="ziggy",
    project_dir="zigproj",     # dir containing build.zig
    exe="ziggy",               # filename under zig-out/bin (default=name)
    zig_args=("-Dextra=1",),   # passed through to `zig build`
)
```

The framework calls `zig build --prefix <our_build_dir>` so outputs
land in our tree. Profile → optimize mode mapping:
`Debug → Debug`, `Release → ReleaseFast`, `ReleaseSafe → ReleaseSafe`.

`lint_cmds` runs `zig fmt --check <project_dir>`.

## ZigTest

Delegates to `zig build test` — compiles and runs the test artifacts
declared in `build.zig`:

```python
ZigTest(
    name="zigappTests",
    project_dir="zigproj",
    zig_args=("--summary", "all"),
)
```

Like `GoogleTest`/`Pytest`, pinned to `arch="host"` so `devops test`
runs on your machine even when production is cross-compiled. Empty
`build_cmds`, all work happens in `test_cmds`.

## Install

Stage a built artifact outside the `build/` tree — see
{doc}`install`.
