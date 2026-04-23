# Remote references

Declare a `libs=` entry using a typed *reference* — `GitRef`, `TarballRef`,
or `DirectoryRef` — and `devops` fetches, builds, and links the
referenced target automatically.

```python
from builder import DirectoryRef, ElfBinary, GitRef, TarballRef, glob

ElfBinary(
    name="app",
    srcs=glob("main.c"),
    libs=[
        GitRef("ssh://git@github.com/acme/libfoo", target="libfoo", ref="v1.2.3"),
        TarballRef("https://releases.example.com/libbar-1.0.tar.gz", target="libbar"),
        DirectoryRef("/opt/shared/libbaz", target="libbaz"),
    ],
)
```

Every ref carries the same required field — `target` — naming the target
inside the referenced project's `build.py`.

## Ref types

### `GitRef`

`git clone` over ssh, https, or a local `file://` path. Optional `ref=`
selects a branch, tag, or sha after clone.

```python
GitRef("ssh://git@github.com/acme/libfoo",   target="libfoo", ref="v1.2.3")
GitRef("https://github.com/acme/libfoo.git", target="libfoo")
GitRef("https://internal.corp/bar.git",      target="libbar", ref="main")
```

Pass the URL as `git` would understand it — **without** the `git+`
prefix; `GitRef` adds that internally.

### `TarballRef`

Tarball at a local path or http(s) URL. Extracted on fetch.

```python
TarballRef("https://example.com/libfoo-1.0.tar.gz", target="libfoo")
TarballRef("./vendor/libbar.tar.gz",                 target="libbar")
TarballRef("/abs/path/pkg.tar.xz",                   target="libpkg")
```

Supported suffixes: `.tar.gz`, `.tgz`, `.tar`, `.tar.xz`, `.tar.bz2`.
Relative paths resolve against the working directory at link time —
prefer absolute paths (e.g. `str(Path(__file__).parent / "vendor/...")`)
when authoring a `build.py`.

### `DirectoryRef`

Local directory, absolute or relative:

```python
DirectoryRef("/opt/shared/libfoo", target="libfoo")
DirectoryRef("./vendor/libbar",    target="libbar")
```

Same relative-path caveat as `TarballRef`.

## includes= also accepts Refs

A `Ref` pointing at a `HeadersOnly` target in an external project becomes
`-I<staged include dir>` at compile time:

```python
ElfBinary(
    name="app",
    srcs=glob("src/*.c"),
    includes=[
        GitRef("ssh://git@github.com/acme/headers", target="PublicHeaders"),
    ],
)
```

The remote is fetched lazily at compile time (same cache as `libs=`),
and the referenced target must be a `HeadersOnly` — anything else
raises `TypeError`.

## python_deps= also accepts Refs

```python
PythonShiv(
    name="app",
    entry="app.cli:main",
    pyproject="app/pyproject.toml",
    python_deps=[
        shared_wheel,                                  # Target instance
        "::shared",                                    # local "::name"
        GitRef("ssh://git@github.com/acme/wheels",
               target="libfoo", ref="v2.0.0"),
    ],
)
```

## Cache

Resolved remotes live under `~/.cache/devops/remotes/<sha1>/`. Delete the
cache directory to force a re-fetch:

```bash
rm -rf ~/.cache/devops/remotes/
```

No network traffic happens during `build.py` import — resolution is
lazy, triggered only when a target is actually linked.

## Remote project namespace

Remote projects show up in `devops describe` under `remote.<name>`:

```
ElfSharedObject remote.libfoo::libfoo (host)
  srcs:     src/...
```

Reference them by their qualified name if there's a clash, e.g.
`remote.libfoo::libfoo`.
