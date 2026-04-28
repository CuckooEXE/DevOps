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

- `inputs=` values can be `Target` (bound as a template view), `Ref`
  (a remote target — resolved at build time, its build commands run
  first), or `str`/`Path` (bound as absolute-path strings); Target
  values flow into `self.deps` for topo-sort and Refs are scheduled
  via the per-run remote prelude
- `outputs=` is a list; reference via `{out[0]}`, `{out[1]}`, …
- Every `cmds` entry is joined under `set -e` into one shell
  invocation — atomic caching, clean failure mid-pipeline
- Unknown template names raise `KeyError` at build time with the known
  set listed

See also {doc}`cross_compile` — each input's `.output_path` and
`.output_dir` correctly reflect the arch the artifact was built for.

## FileArtifact

Copy a single file into the build tree. The `src` may be a literal
path or another Artifact whose output is a file:

```python
default_conf = FileArtifact(
    name="default_conf",
    src="etc/myapp.conf",      # path resolved against project root
    dest="config/app.conf",    # optional rename; default is src basename
    mode="0644",               # optional chmod after copy
)

# Or copy a built artifact's output (codegen, post-processing, etc.):
FileArtifact(name="copy_app", src=myApp)
```

`src` can also be a `Ref` (`GitRef` / `TarballRef` / `DirectoryRef`) —
the remote project is fetched and built before the copy runs.

Output: `build/<profile>/<project>/<name>/<dest>`

## DirectoryArtifact

Recursively copy a directory into the build tree. Always uses
`shutil.copytree(symlinks=True, copy_function=copy2)` (preserves
symlinks, modes, and timestamps). The destination is wiped before each
copy so removed sources don't linger across rebuilds.

```python
assets = DirectoryArtifact(
    name="assets",
    src="static/assets",
    file_mode="0644",     # optional: chmod every regular file
    dir_mode="0755",      # optional: chmod every directory
)
```

`src` may also be an `Artifact` (e.g. another `DirectoryArtifact` or
`HeadersOnly` whose output is a directory) or a `Ref`.

Cache caveat: a path-typed `src` is walked once per `devops`
invocation. Files added between invocations are picked up; files added
during a single `watch` session that reuses the configured graph are
not.

## CompressedArtifact

Bundle files / directories / Targets into a single archive. The
mapping interface places arbitrary inputs at arbitrary archive paths:

```python
release = CompressedArtifact(
    name="release",
    format=CompressionFormat.TarGzip,    # Gzip / TarGzip / Zip
    archive_name="myapp-1.0",            # optional stem; default is `name`
    entries={
        "bin/myapp":         myApp,             # Artifact
        "include":           myHeaders,         # HeadersOnly
        "config/app.conf":   "etc/myapp.conf",  # path
        "share/data":        "data",            # directory path
        "third_party/lib":   GitRef(            # remote Artifact
            url="https://github.com/acme/libfoo",
            target="libfoo_static",
            ref="v1.2.3",
        ),
    },
)
```

- `format=CompressionFormat.Gzip` requires exactly one entry whose
  source is a regular file — gzip wraps a single file with no internal
  layout (the archive path is ignored).
- Archives are byte-reproducible: gzip mtime is pinned to 0,
  `TarInfo` per-entry mtime/uid/gid are zeroed, and zip entries use the
  DOS epoch for `date_time`. Two builds from byte-identical inputs
  produce identical archives.
- The archive is written by a Python helper invoked under
  `sys.executable` — no `tar`/`gzip`/`zip` required on PATH.

Output: `build/<profile>/<project>/<name>/<archive_name>.<ext>`
(`.gz`, `.tar.gz`, or `.zip`).

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
