# Architecture

A short tour of the moving parts. Source under `devops/` unless noted.

## The user-facing surface: `builder/`

`builder/__init__.py` re-exports everything a `build.py` imports. It's
intentionally thin so the stable contract is small.

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
- **`cache.py`** — stamp-file-based incremental build. Each Command's
  first output gets a `<output>.stamp` next to it, containing
  `sha256(argv + input mtimes)`. A Command is "fresh" if the stamp
  matches and every declared output exists.

## Built-in targets: `devops/targets/`

- **`c_cpp.py`** — `CCompile` mixin + `ElfBinary`, `ElfSharedObject`,
  `StaticLibrary`, `HeadersOnly`. The `CCompile._compile_flags()`
  method is the single source of truth for flags; build AND lint both
  call it.
- **`python.py`** — `PythonWheel` wrapping `python -m build`.
- **`docs.py`** — `SphinxDocs` wrapping `sphinx-build`.
- **`script.py`** — re-exports `Script` from `core.target`.
- **`tests.py`** — `TestTarget` (marker base), `GoogleTest`, `Pytest`.

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

Loaded from `devops.toml` at workspace root.

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
