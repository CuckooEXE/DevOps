# devops-example-tarball

A minimal runnable devops plugin. Copy this directory as scaffolding
for your own plugin — it's ~80 lines of Python + a 20-line pyproject.

## What it provides

`TarballArtifact(name, srcs=[...])` — bundles `srcs` into
`<output-dir>/<name>.tar.gz` using the system `tar`. Uses the
`devops.api` surface only, so it rides every bump to the framework
until the API version changes.

## Install

From the devops repo root:

```sh
pip install -e ./examples/devops-example-tarball
```

(Or from anywhere, if you've published your plugin: `pip install
devops-example-tarball`.)

## Use

In any devops project's `build.py`:

```python
from builder import TarballArtifact, glob

TarballArtifact(
    name="release",
    srcs=glob("dist/**/*.so") + ["README.md"],
    doc="All shared libs + release notes.",
)
```

Then:

```sh
devops build release
# → build/Debug/host/<proj>/release/release.tar.gz
```

## How it works

Three things make this a plugin rather than a one-off target class:

1. **Entry point in `pyproject.toml`**:

   ```toml
   [project.entry-points."devops.targets"]
   tarball = "devops_example_tarball:register"
   ```

   devops scans this group on every `builder` import.

2. **`register(api)` callable** in `__init__.py`:

   ```python
   def register(api):
       api.register_target(TarballArtifact)
       api.DEFAULT_TOOLCHAIN_EXTRAS.setdefault("tar", Tool.of("tar"))
   ```

   First line makes the class importable from `builder`. Second
   seeds a default `tar` tool that users can override in their
   `devops.toml`:

   ```toml
   [toolchain.extras]
   tar = ["docker", "run", "--rm", "ghcr.io/acme/tar", "tar"]
   ```

3. **API version declaration**:

   ```python
   MIN_API_VERSION = "1"
   ```

   devops warns-and-skips plugins whose minimum is above the running
   `devops.api.API_VERSION`, so a version bump fails loudly instead
   of silently breaking.

See `docs/developer_guide/writing_a_plugin.md` in the devops repo for
the full reference.
