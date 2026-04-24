# Running targets from a remote ref

`devops run`, `devops build`, and `devops describe` all accept a
**remote-ref spec** in place of a local target name. From any
directory — no workspace required — you can fetch a project, build a
target from it, and execute it:

```sh
devops run git+ssh://github.com/acme/tools@v1.2::mycli -- --flag=val
devops build https://example.com/archive.tar.gz::libfoo
devops describe /abs/path/to/project::MyTarget
```

## Spec grammar

`<source>::<TargetName>` — the source is one of:

| Prefix                     | Meaning                                          |
|----------------------------|--------------------------------------------------|
| `git+ssh://host/path[@ref]`  | git clone over ssh; optional branch/tag/sha       |
| `git+https://host/path[@ref]`| git clone over https                              |
| `git+file:///abs/path[@ref]` | git clone from a local bare repo                  |
| `https://.../x.tar.gz`     | http(s) tarball download                         |
| `file:///abs/path/...`     | local directory or tarball                       |
| `/abs/path`                | local directory                                  |
| `./relative` / `../path`   | local directory relative to cwd                  |

Anything that doesn't match one of these (e.g. `MyTarget` or
`project::MyTarget`) is treated as a local-workspace target name,
same as before.

## What happens under the hood

1. The source is fetched (or just read for local paths) into
   `~/.cache/devops/remotes/<sha1(url)[:16]>/`. Subsequent runs with
   the same URL reuse the cache.
2. The fetched project's `build.py` is imported — its targets register
   against a synthetic `remote.<name>` project.
3. A `BuildContext` is built with `workspace_root` = the fetched dir
   and `build_dir` = `~/.cache/devops/run/<sha1(spec)[:16]>/build/`.
   Toolchains come from the remote's `devops.toml` if present, else
   the built-in defaults.
4. The target builds transitively (the cache makes the second run
   near-instant).
5. For `devops run`: the resulting `Artifact`'s output is exec'd from
   **your current working directory** with any positional args you
   passed after the spec. A `Script` runs its cmds directly.

## Passing args to the binary

Arguments after the spec are forwarded:

```sh
devops run git+ssh://host/acme/mycli::mycli -- --flag=val arg1
```

The `--` separator is recommended whenever you pass flags — without
it, Typer may try to interpret a leading `-` as a `devops` option.

## Cache invalidation

- First run clones; subsequent runs reuse the cache.
- `@main` or any mutable ref caches against the ref string. If
  `main` advances and you need the new HEAD, delete
  `~/.cache/devops/remotes/<hash>/` to force a re-fetch.
- Pin to a sha (`@abc1234`) for stable runs.

## Troubleshooting

Remote builds need every tool their `build.py` declares. If a build
halts with "command not found", install the tool (or add it to the
toolchain via a `devops.toml` override) and retry — the cache picks
up where it left off.

## Security

`build.py` is Python. Running `devops run git+...` executes arbitrary
code from the source URL at import time. Use it for your own tooling
only, or after auditing the ref.
