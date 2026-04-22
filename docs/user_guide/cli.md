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

## `devops run <name> [--dry-run]`

Execute an artifact's binary, or run a Script. Script deps build first.

```bash
devops run MyCoolApp           # exec the binary
devops run pushToProd          # run a Script
devops run pushToProd --dry-run   # print the cmds, don't run
```

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

## `devops test [names...] [--profile ...]`

Build and run every test target (default: all; else the named subset).
Tests inherit their target's compile environment for C/C++, or set
`PYTHONPATH` for Python. Non-zero exit on any failure.

## `devops version <name>`

Print the artifact's version. Falls back through:
`version="..."` kwarg → project's `VERSION` file → `git describe --tags
--always --dirty` → `"0.0.0-unknown"`.

## `devops clean [names...]`

Remove build outputs for selected (or all) artifacts. Deletes the
artifact's output directory.
