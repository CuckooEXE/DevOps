# Remote references

Declare a `libs=` entry using one of four URL schemes and `devops` fetches,
builds, and links the referenced target automatically.

## Syntax

```
<url>[@<ref>]::<TargetName>
```

The trailing `::<TargetName>` picks a target from the remote project's
`build.py`; the optional `@<ref>` (git only) picks a branch, tag, or sha.

## Supported schemes

### `file://`

Local directory or tarball. Absolute or relative:

```python
ElfBinary(
    name="app",
    srcs=glob("main.c"),
    libs=[
        "file:///opt/shared/libfoo::libfoo",       # absolute dir
        "file://./vendor/libbar.tar.gz::libbar",   # relative tarball
    ],
)
```

Accepted tarball suffixes: `.tar.gz`, `.tgz`, `.tar`, `.tar.xz`,
`.tar.bz2`.

### `git+ssh://`

Clone over SSH. Optional `@<ref>`:

```python
libs=[
    "git+ssh://git@github.com/acme/libfoo@v1.2.3::libfoo",
    "git+ssh://git@internal.corp/libbar::libbar",   # default branch
]
```

Also works for `git+file://` / `git+https://` variants — the `git+`
prefix is stripped and the rest handed to `git clone`.

### `http://` / `https://`

Download a tarball and extract:

```python
libs=[
    "https://releases.example.com/libfoo-1.2.3.tar.gz::libfoo",
]
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
