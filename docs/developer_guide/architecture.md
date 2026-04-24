# Architecture

A short tour of the moving parts. Source under `devops/` unless noted.

## The user-facing surface: `builder/`

- **`builder/__init__.py`** re-exports the core target types a
  `build.py` imports (`ElfBinary`, `Script`, `Install`, etc.).
  Intentionally thin so the stable contract stays small.
- **`builder/plugins`** is a submodule populated at import time from
  installed plugins' entry points (see `devops/plugins.py`). User
  build.py files write `from builder.plugins import FooTarget` for
  plugin-contributed types, keeping core vs plugin obvious at every
  call site.

## Core types: `devops/core/`

- **`target.py`** — `Target`, `Artifact`, `Script`, `Project`,
  `_TargetView`. These are the abstract classes users subclass and the
  concrete Script class.
- **`command.py`** — `Command` is a frozen dataclass recording argv,
  cwd, env, inputs, outputs. Everything that runs in a subprocess comes
  out of a target's `build_cmds()` / `lint_cmds()` / `test_cmds()` /
  `run_cmds()` as a list of `Command`s.
- **`runner.py`** — Executes a list of `Command`s, with dry-run,
  tool-missing detection, and cache integration.

## Registry and workspace: `devops/{registry,workspace,graph,cache}.py`

- **`registry.py`** — process-global store of every `Target` registered
  during a build.py import. Cleared on each CLI invocation. Provides
  name resolution (`resolve("MyApp")`, `resolve("::local")`,
  `resolve("project::qualified")`).
- **`workspace.py`** — finds the workspace root (`devops.toml` or
  `.git`) and imports every `build.py` underneath, activating the
  correct Project in the registry during exec.
- **`graph.py`** — topological sort over `Target.deps`. Raises on
  cycles.
- **`graph_export.py`** — renders the registered graph as dot / json
  / text for `devops graph`. Edge kinds are recovered from the dep
  key prefix (`_lib_`, `_inc_`, `_obj_`, `_in_`, `_install_`).
- **`cache.py`** — stamp-file-based incremental build. Each Command's
  first output gets a `<output>.stamp` next to it, containing
  `sha256(argv + input mtimes)`. A Command is "fresh" if the stamp
  matches and every declared output exists.
- **`watch.py`** — rebuild-on-change inner loop. Builds a reverse
  index over every target's inputs + depfile-discovered headers,
  debounces editor-save bursts, and re-runs affected targets
  through the same cache layer. Watchdog if installed, polling
  fallback otherwise.

## Built-in targets: `devops/targets/`

- **`c_cpp.py`** — `CCompile` mixin + `ElfBinary`, `ElfSharedObject`,
  `StaticLibrary`, `HeadersOnly`, `CObjectFile`, `LdBinary`. The
  `CCompile._compile_flags()` method is the single source of truth
  for flags; build AND lint both call it.
- **`python.py`** — `PythonWheel`, `PythonApp`, `PythonShiv`.
- **`docs.py`** — `SphinxDocs` wrapping `sphinx-build`.
- **`custom.py`** — `CustomArtifact`: arbitrary shell commands with
  templated inputs/outputs. The escape hatch when nothing else fits.
- **`install.py`** — `Install`: stage binaries/libs under a dest, or
  pip-install wheels.
- **`zig.py`** — `ZigBinary`, `ZigTest` delegating to `zig build`.
- **`script.py`** — re-exports `Script` from `core.target`.
- **`tests.py`** — `TestTarget` (marker base), `GoogleTest`, `Pytest`.

Additional target types (e.g. libvirt-backed e2e via
`TestRangeTest`) ship as separate plugins under `plugins/`.

## Lint tools: `devops/tools/`

- **`clang.py`** — `clang-tidy`, `clang-format`, `cppcheck` command
  builders. They consume a `CCompile` target's `_compile_flags(ctx)`
  verbatim, so the lint invocation sees the same flags as the build.
- **`python_tools.py`** — `black --check`, `ruff check`.

## Toolchain: `devops/context.py`

`BuildContext` bundles workspace root, build dir, profile, verbosity,
and a `Toolchain`. `Toolchain` is a dataclass of `Tool`s; each `Tool`
carries an argv prefix (so `cc` can be `["docker", "run", ...,
"clang"]`). Placeholders `{workspace}`, `{project}`, `{cwd}` are
expanded per-Command via `Tool.resolved_for(...)`.

`Toolchain.extras: dict[str, Tool]` is the plugin namespace — plugins
seed defaults at register time via `DEFAULT_TOOLCHAIN_EXTRAS` and
users override per-project / per-arch in `[toolchain.extras]` /
`[toolchain.<arch>.extras]` tables.

Loaded from `devops.toml` at workspace root.

## Remote refs + ad-hoc runs: `devops/{remote,remote_run}.py`

- **`remote.py`** — `GitRef` / `TarballRef` / `DirectoryRef` for
  external-project deps. Lazy fetch at link time into
  `~/.cache/devops/remotes/<hash>/`. Referenced from `libs=` /
  `includes=` on C/C++ targets.
- **`remote_run.py`** — parses a CLI spec string
  (`git+ssh://…::Target`, etc.) into the matching `Ref`, and builds
  an ad-hoc `BuildContext` so `devops run <remote-spec>` /
  `devops build <remote-spec>` / `devops describe <remote-spec>`
  work from any cwd without a local workspace.

## Plugins: `devops/{api,plugins,testing}.py`

- **`api.py`** — stable import surface plugin authors depend on.
  `API_VERSION = "1"`; plugins declare `MIN_API_VERSION` and the
  loader warns-and-skips incompatible ones.
- **`plugins.py`** — discovers the `devops.targets` entry-point
  group, calls each plugin's `register(api)` hook (or registers a
  bare Target class), caches the result process-locally. Errors are
  warn-and-skip by default; `DEVOPS_STRICT_PLUGINS=1` escalates to
  hard failures for CI.
- **`testing.py`** — helpers for plugin authors: `make_ctx(...)`,
  `active_project(...)`, `assert_command_shape(...)`.

## CLI: `devops/cli.py`

Typer-based dispatch. Each subcommand:

1. Walks up to find workspace root
2. Calls `workspace.discover_projects()` to populate the registry
3. Resolves target names
4. Emits `Command`s via target methods
5. Runs them through `runner.run_all(...)`

Completion callbacks (`_complete_artifact`, `_complete_runnable`,
`_complete_testable`, `_complete_any_target`) use the same workspace
discovery to populate `<TAB>` output.

## Flow of a single `devops build MyCoolApp`

1. `cli.build()` → `_prepare()` finds workspace, imports all
   `build.py` files, loads `devops.toml`.
2. `_resolve("MyCoolApp")` returns the registered `ElfBinary` instance.
3. `graph.topo_order([MyCoolApp])` yields deps first — libraries,
   headers — then MyCoolApp.
4. For each Artifact in order, `build_cmds(ctx)` returns a list of
   `Command`s. `runner.run_all(...)` executes them; each one is cached
   via `.stamp` files so reruns no-op when inputs are unchanged.
5. Final output path is `build/<profile>/<project>/<name>/<name>`.
