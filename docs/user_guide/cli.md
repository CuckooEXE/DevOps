# CLI reference

Every subcommand walks up from the current directory to find a
`devops.toml` (or `.git`) to treat as the workspace root, imports every
`build.py` below it, then operates on the resulting target graph.

Tab completion: `devops --install-completion <shell>`, then reload your
shell.

## `devops describe [names...]`

Print every target (or just selected ones) with its sources, flags,
dependencies, and `doc=` description.

```bash
devops describe
devops describe MyCoolApp
devops describe project::name
```

## `devops build <name> [--profile ...] [--verbose]`

Build an artifact. Transitive deps build first (in topo order).

```bash
devops build MyCoolApp
devops build MyCoolApp --profile ReleaseSafe
devops build MyCoolApp -v
```

Profiles: `Debug` (default, `-O0 -ggdb`), `Release` (`-O2 -DNDEBUG`),
`ReleaseSafe` (`-O2 -ggdb -D_FORTIFY_SOURCE=2 -fstack-protector-strong`).

## `devops run <spec> [args...] [--dry-run]`

Execute an artifact's binary, or run a Script. Script deps build first.

```bash
devops run MyCoolApp                  # exec the binary
devops run MyCoolApp -- --verbose x   # args after `--` forwarded to the binary
devops run pushToProd                 # run a Script
devops run pushToProd --dry-run       # print the cmds, don't run
```

`<spec>` may also be a **remote-ref spec** (`git+ssh://host/repo[@ref]::Target`,
`https://host/x.tar.gz::Target`, `/abs/path::Target`, `./rel::Target`) —
devops fetches/clones the source, imports its `build.py`, builds the
target transitively, and execs from your current cwd. The same remote
form is accepted by `devops build` and `devops describe`. See
{doc}`remote_run` for the full grammar.

Libraries (`ElfSharedObject`, `StaticLibrary`, `HeadersOnly`) are not
runnable and raise an error.

## `devops cmds <name> [--profile ...]`

Print the shell commands that `devops build` would run, without running
them. Useful for debugging and generating `compile_commands.json`-style
output.

```bash
devops cmds MyCoolApp
devops cmds MyCoolApp --profile Release
```

## `devops lint [names...] [--profile ...]`

Run every artifact's `lint_cmds()`. Default is all artifacts. For
C-family targets that's `clang-tidy`, `clang-format --dry-run --Werror`,
`cppcheck`; for PythonWheels it's `black --check` + `ruff check`; for
SphinxDocs it's `sphinx-build -Q -W -n`.

Missing tools surface as a single typed failure per target rather than a
crash, so a clean `devops lint` locally doesn't require every team
member to have every tool installed.

## `devops bootstrap [--verbose] [--dry-run]`

Install tools declared in `devops.toml`'s `[bootstrap]` section — the
companion to `doctor`, for fresh VMs / CI runners / Dockerfile layers.
Runs `apt` → `pip` → `run` (verbatim shell). See {doc}`bootstrap` for
the schema.

```bash
devops bootstrap             # install everything
devops bootstrap --dry-run   # print what would run
devops bootstrap -v          # verbose summary of each list
```

## `devops doctor [--profile ...] [--verbose]`

Pre-flight check: walks every registered target, unions declared
`required_tools=` with the `argv[0]` of every non-shell `Command` the
targets produce, and resolves each through `shutil.which`. Exits
non-zero with a consolidated report if any are missing.

```bash
devops doctor             # silent on success
devops doctor -v          # per-tool report, including who needs it
```

Run this **before** `devops build` in CI — a missing tool fails at
pre-flight instead of mid-compile. When `[bootstrap]` is defined, the
error output suggests `devops bootstrap` as the fix. For
`CustomArtifact` / `Script` targets whose commands are shell strings,
declare the tools they need via `required_tools=[...]` so they show up
here.

## `devops install [names...] [--profile ...]`

Run selected (or all) `Install` targets — stage binaries/libs under a
destination directory, or pip-install wheels. The Install target builds
its artifact first, so this does the right thing without a prior
`devops build`.

See {doc}`install`.

## `devops test [names...] [--profile ...]`

Build and run every test target (default: all; else the named subset).
Tests inherit their target's compile environment for C/C++, or set
`PYTHONPATH` for Python. Non-zero exit on any failure.

## `devops version <name>`

Print the artifact's version. Falls back through:
`version="..."` kwarg → project's `VERSION` file → `git describe --tags
--always --dirty` → `"0.0.0-unknown"`.

## `devops graph [names...] [--format=dot|json|text] [--output=path] [--resolve-remotes]`

Export the dependency DAG. With no names, dumps the whole workspace;
with names, dumps the forward-transitive subgraph rooted there. Remote
refs are opaque by default — pass `--resolve-remotes` to fetch and
inline them. Full reference: {doc}`graph`.

```bash
devops graph                          # stdout, dot format
devops graph | dot -Tsvg > g.svg
devops graph MyCoolApp --format=json  # machine-readable
```

## `devops watch [names...] [--debounce-ms=...] [--clear-screen] [--poll]`

Build once, then rebuild affected targets on file change. Walks every
Command input plus headers discovered from depfiles and extends the
affected set through `Target.deps` so consumers rebuild automatically.
`build.py` / `devops.toml` edits trigger an in-process re-discovery.
Watchdog is an optional dep (install via `pip install
'devops-builder[watch]'`); without it, a polling fallback kicks in.
Full reference: {doc}`watch`.

```bash
devops watch                          # watch every Artifact
devops watch MyCoolApp                # only rebuild one target + its consumers
devops watch --poll                   # force mtime polling
```

## `devops clean [names...]`

Remove build outputs for selected (or all) artifacts. Deletes the
artifact's output directory — including any auto-managed venv (for
`PythonApp`), cached wheels (for `PythonShiv`), or stamp files.
