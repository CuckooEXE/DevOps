"""Stable API surface for devops plugins.

A plugin is a Python package that registers Target subclasses via the
``devops.targets`` entry-point group. Plugin authors import from this
module — never from ``devops.core.*`` or ``devops.context`` directly
— so changes internal to the framework don't break out-of-tree code.

Minimal plugin (packaged as its own ``pip``-installable distribution)::

    # acme_devops_rust/__init__.py
    from devops.api import Artifact, BuildContext, Command, Tool, register_target, DEFAULT_TOOLCHAIN_EXTRAS

    class RustBinary(Artifact):
        def __init__(self, name, srcs, **kw):
            super().__init__(name=name, **kw)
            self.srcs = tuple(srcs)

        def build_cmds(self, ctx: BuildContext):
            cargo = ctx.toolchain_for(self.arch).extras["cargo"]
            return [Command(
                argv=cargo.invoke(["build", "--release"]),
                cwd=self.project.root,
                label=f"cargo build {self.name}",
                inputs=tuple(self.srcs),
                outputs=(self.output_path(ctx),),
            )]

        def output_path(self, ctx: BuildContext):
            return self.output_dir(ctx) / self.name

        def describe(self):
            return f"RustBinary {self.qualified_name}"

    def register(api):
        api.register_target(RustBinary)
        api.DEFAULT_TOOLCHAIN_EXTRAS["cargo"] = api.Tool.of("cargo")

And in its ``pyproject.toml``::

    [project.entry-points."devops.targets"]
    rust = "acme_devops_rust:register"

The plugin exposes an entry point that points at either a Target
subclass (the class itself) or a ``register(api)`` callable that
installs one or more classes.

**Compatibility:** check ``API_VERSION`` at the top of this module.
Plugins may declare ``MIN_API_VERSION = "1"`` at module top level;
the loader warns-and-skips plugins that declare a higher minimum
than the running devops exports.
"""

from __future__ import annotations

from devops.context import HOST_ARCH, BuildContext, Tool, Toolchain
from devops.core.command import Command
from devops.core.target import Artifact, Project, Script, Target, _TargetView
from devops.options import OptimizationLevel
from devops.remote import DirectoryRef, GitRef, Ref, TarballRef


API_VERSION = "1"
"""Major version of this API surface. Plugins declaring a ``MIN_API_VERSION``
higher than this string are skipped with a warning. Bump on breaking changes:
removed classes, renamed methods, changed signatures.
"""


DEFAULT_TOOLCHAIN_EXTRAS: dict[str, Tool] = {}
"""Populated by plugin ``register()`` hooks. Each entry becomes a default
on every loaded ``Toolchain`` (user-supplied ``[toolchain.extras]`` in
``devops.toml`` wins). Plugin Targets read their tool via
``ctx.toolchain_for(self.arch).extras["<name>"]``."""


_REGISTERED_TARGET_CLASSES: list[type[Target]] = []


def register_target(cls: type[Target]) -> type[Target]:
    """Register a Target subclass for injection into the ``builder`` module.

    Returns the class so it can be used as a decorator:

        @api.register_target
        class RustBinary(api.Artifact):
            ...
    """
    if not isinstance(cls, type) or not issubclass(cls, Target):
        raise TypeError(f"register_target expects a Target subclass, got {cls!r}")
    _REGISTERED_TARGET_CLASSES.append(cls)
    return cls


def _registered_classes() -> list[type[Target]]:
    return list(_REGISTERED_TARGET_CLASSES)


__all__ = [
    "API_VERSION",
    "DEFAULT_TOOLCHAIN_EXTRAS",
    "Artifact",
    "BuildContext",
    "Command",
    "DirectoryRef",
    "GitRef",
    "HOST_ARCH",
    "OptimizationLevel",
    "Project",
    "Ref",
    "Script",
    "TarballRef",
    "Target",
    "Tool",
    "Toolchain",
    "_TargetView",
    "register_target",
]
