# Writing a devops plugin

A plugin is a normal Python package that registers new `Target`
subclasses via an entry point. Once `pip install`ed into the same
environment as `devops`, its classes become importable from
`builder`:

```python
# your_project/build.py
from builder import RustBinary    # from the plugin
```

Use a plugin when:

- You're shipping target types to **other people** (an internal PyPI
  server, open source, etc.).
- The target set is specific to a technology stack that doesn't
  belong in the devops core (Rust, Go, Haskell, your company's
  proprietary codegen).

If the target is only ever used inside your own workspace, just
subclass in a `build.py` helper module — no packaging required.

## Anatomy

A plugin package has:

1. **A Target subclass** that inherits from `devops.api.Artifact`
   (or `Target` for non-output-producing work).
2. **Either** a `register(api)` callable that installs the class
   and any default tools, **or** a bare class reference at the
   entry point (shortest path for single-class plugins).
3. **A `pyproject.toml`** declaring the entry point under the
   `devops.targets` group.

### Minimal `register(api)` plugin

```python
# acme_devops_rust/__init__.py
from devops.api import (
    Artifact,
    BuildContext,
    Command,
    register_target,
    DEFAULT_TOOLCHAIN_EXTRAS,
    Tool,
)

MIN_API_VERSION = "1"


@register_target
class RustBinary(Artifact):
    def __init__(self, name: str, srcs, **kw):
        super().__init__(name=name, **kw)
        self.srcs = tuple(srcs)

    def build_cmds(self, ctx: BuildContext):
        cargo = ctx.toolchain_for(self.arch).extras["cargo"]
        out = self.output_path(ctx)
        return [Command(
            argv=cargo.invoke(["build", "--release", "--target-dir", str(out.parent)]),
            cwd=self.project.root,
            label=f"cargo build {self.name}",
            inputs=tuple(self.srcs),
            outputs=(out,),
        )]

    def output_path(self, ctx: BuildContext):
        return self.output_dir(ctx) / self.name

    def describe(self):
        return f"RustBinary {self.qualified_name}"


def register(api):
    # Register_target already called via the decorator above. Install a
    # default tool so users don't have to configure it by hand.
    api.DEFAULT_TOOLCHAIN_EXTRAS.setdefault("cargo", api.Tool.of("cargo"))
```

### `pyproject.toml`

```toml
[project]
name = "acme-devops-rust"
version = "0.1.0"
dependencies = ["devops-builder"]

[project.entry-points."devops.targets"]
rust = "acme_devops_rust:register"
```

The right-hand side of the entry-point line is
`<module>:<attribute>`. Point it at your `register` function, or
directly at a Target class for the shortest possible plugin.

## The API surface

`devops.api` re-exports everything plugins need. Import **only** from
there. Internal modules (`devops.core.*`, `devops.context`) are not
covered by compatibility guarantees.

| Import                                | What                                                 |
|---------------------------------------|------------------------------------------------------|
| `Target`, `Artifact`, `Script`        | Base classes.                                         |
| `Command`                             | The recipe dataclass.                                 |
| `BuildContext`, `Toolchain`, `Tool`   | Read via `ctx.toolchain_for(arch)`.                   |
| `HOST_ARCH`                           | The default arch string.                              |
| `OptimizationLevel`                   | `Debug` / `Release`.                                  |
| `Ref`, `GitRef`, `TarballRef`, `DirectoryRef` | If your target accepts remote deps.            |
| `register_target`                     | Install a Target class.                               |
| `DEFAULT_TOOLCHAIN_EXTRAS`            | Seed default tools (users override in devops.toml).   |
| `API_VERSION`                         | Current API major version.                            |

## Toolchain integration

Your target probably needs a tool (compiler, formatter, test runner)
that isn't built into `Toolchain`. Use the `extras` namespace:

```python
# in register():
api.DEFAULT_TOOLCHAIN_EXTRAS["cargo"] = api.Tool.of("cargo")

# in build_cmds():
cargo = ctx.toolchain_for(self.arch).extras["cargo"]
```

Users can override per-arch in their `devops.toml`:

```toml
[toolchain.extras]
cargo = ["docker", "run", "--rm", "ghcr.io/acme/cargo:v1", "cargo"]

[toolchain.aarch64.extras]
cargo = ["cross", "build"]
```

`devops doctor` walks every Target's `build_cmds` and picks up
`argv[0]`, so your plugin's tool needs are pre-flighted
automatically.

## Version compatibility

Declare `MIN_API_VERSION = "1"` at your module's top level. A
devops running a lower `API_VERSION` than your minimum gets a
warn-and-skip, not a crash:

```
devops: plugin 'rust' requires api version 2, devops provides 1 — skipping
```

When devops bumps `API_VERSION` to "2" (breaking change), update your
plugin and ship a new version.

## Error handling

By default, a plugin that fails to import — or whose `register()`
raises — is skipped with a warning. The rest of the user's build
graph is unaffected. Set `DEVOPS_STRICT_PLUGINS=1` to escalate
those warnings to hard failures; useful in CI where you want any
plugin breakage to fail the build.

## Testing your plugin

`devops.testing` ships two helpers:

```python
from pathlib import Path
from devops.api import Project
from devops.testing import active_project, make_ctx, assert_command_shape

from acme_devops_rust import RustBinary


def test_rust_build_cmds(tmp_path: Path):
    (tmp_path / "src/main.rs").parent.mkdir(parents=True)
    (tmp_path / "src/main.rs").write_text("fn main() {}\n")

    proj = Project(name="t", root=tmp_path)
    with active_project(proj):
        t = RustBinary(name="mycli", srcs=[tmp_path / "src/main.rs"])

    ctx = make_ctx(tmp_path)
    # Tests typically need the tool in extras; set up a fake:
    ctx.toolchain.extras["cargo"] = ctx.toolchain.cc  # any Tool
    assert_command_shape(
        t.build_cmds(ctx),
        argv_contains=["build"],
        inputs_include=[tmp_path / "src/main.rs"],
    )
```

## Inspecting loaded plugins

`devops doctor --verbose` lists every loaded plugin, its declared
`min_api_version`, and the classes it contributed. Use it to debug
"why isn't my plugin showing up" questions without adding
`print`-statements.

## Gotchas

- **Plugins don't opt into the stamp cache** — it's automatic. You
  just need to declare correct `inputs=` / `outputs=` / `depfile=` on
  every Command. The cache does the rest.
- **Dynamic injection into `builder`** means `mypy --strict` can't
  see your plugin's classes when imported as `from builder import
  RustBinary`. Either import from your plugin module directly, or
  ship a `py.typed` marker and document the tradeoff.
- **Name collisions with built-ins** (`ElfBinary`, etc.) are
  rejected; the built-in wins and the plugin class is dropped with a
  warning. Prefix class names with your plugin's namespace
  (`AcmeRustBinary`) if conflict is a risk.
