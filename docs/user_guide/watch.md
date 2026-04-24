# devops watch

Inner-loop dev mode. Run a build once, then rebuild whatever is
affected as soon as you save a file.

```sh
devops watch                     # everything
devops watch MyCoolApp           # one target (and its consumers)
devops watch MyCoolApp MyCoolLib # several, deduped
```

Ctrl-C exits.

## What gets watched

Every path that the build actually touches — the source files each
Command declares as inputs, plus every header the compiler's depfile
points at. Because the depfile is populated only after a Command
runs, the **first** build is full; the **second** onward is
incremental and tracks headers you didn't explicitly list in
`includes=`.

Paths under `ctx.build_dir` are dropped so our own outputs don't
trigger rebuild loops.

## What gets rebuilt

- Direct hit: changed path → every Target that had it as input.
- Forward closure: that set is expanded through `Target.deps` so
  consumers also rebuild.
- Scope filter: if you passed explicit names, unrelated subtrees are
  ignored even if they change.

The stamp cache in `devops/cache.py` has the last word on what
actually re-executes — the watcher errs on the side of overrequest
and lets the cache short-circuit redundant work.

## build.py reload

Editing any `build.py` (or `devops.toml`) triggers a full in-process
re-discovery: the registry is reset and every `build.py` is
re-imported. No subprocess re-exec, no restart.

## Flags

| Flag             | Default | Meaning                                      |
|------------------|---------|----------------------------------------------|
| `--profile`      | Debug   | Build profile passed to each rebuild.        |
| `--verbose`/`-v` | off     | Stream every Command's argv.                 |
| `--debounce-ms`  | 250     | Coalesce editor-save bursts.                 |
| `--clear-screen` | off     | ANSI-clear before each rebuild.              |
| `--poll`         | off     | Force mtime polling (skip watchdog).         |

## Install watchdog

`watchdog` is an optional dep for low-latency filesystem events:

```sh
pip install 'devops-builder[watch]'
```

Without it, `devops watch` falls back to a ~1s polling loop — works
fine on small trees, slower to notice changes on large ones. You can
always force polling with `--poll` if watchdog misbehaves in your
environment (NFS, certain containers).

## Errors don't kill the watcher

A failed rebuild is printed and the watcher keeps waiting — fix the
code, save, watch the rebuild retry. Only Ctrl-C exits.
