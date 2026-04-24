"""Plugin discovery + loading via Python entry points.

Entry-point group: ``devops.targets``. Each entry may resolve to:

- A ``Target`` subclass (registered directly).
- A ``register(api)`` callable invoked at load time, which uses
  ``api.register_target`` / ``api.DEFAULT_TOOLCHAIN_EXTRAS`` to install
  one or more Target classes and/or tool defaults.

Import failures, version mismatches, and register() exceptions are
caught and reported to stderr by default — a single broken plugin
doesn't wipe out the rest of the user's build graph. Set
``DEVOPS_STRICT_PLUGINS=1`` to escalate to hard failures (useful in
CI where a broken plugin should stop the build outright).

Load is idempotent: ``load_plugins()`` is safe to call multiple times;
the second and subsequent calls return the cached result.
"""

from __future__ import annotations

import importlib.metadata as _metadata
import os
import sys
from dataclasses import dataclass, field

from devops.api import API_VERSION
from devops.core.target import Target


ENTRY_POINT_GROUP = "devops.targets"


@dataclass
class LoadedPlugin:
    name: str
    module: str
    min_api_version: str
    classes: list[type[Target]] = field(default_factory=list)


_loaded: list[LoadedPlugin] | None = None


def _compare_api_version(min_version: str) -> bool:
    """True if ``min_version`` is compatible with ``API_VERSION``.

    Compares only the major component, so a plugin that declares
    ``MIN_API_VERSION = "1.2"`` is accepted as long as devops's major
    version is ≥ 1. When we bump to a ``"2"``-generation API this
    logic will need proper semver handling.
    """
    try:
        min_major = int(min_version.split(".", 1)[0])
        cur_major = int(API_VERSION.split(".", 1)[0])
    except (ValueError, IndexError):
        return False
    return min_major <= cur_major


def _is_strict() -> bool:
    return os.environ.get("DEVOPS_STRICT_PLUGINS", "").lower() in ("1", "true", "yes")


def _warn(msg: str) -> None:
    print(f"devops: {msg}", file=sys.stderr)


def _collect_registered_since(baseline: int) -> list[type[Target]]:
    """Classes added to ``api._REGISTERED_TARGET_CLASSES`` since baseline."""
    from devops.api import _registered_classes

    return _registered_classes()[baseline:]


def _load_one(ep: _metadata.EntryPoint) -> LoadedPlugin | None:
    """Resolve one entry point and register its classes. Returns None on failure."""
    from devops import api

    try:
        obj = ep.load()
    except Exception as e:
        msg = f"plugin {ep.name!r} failed to import: {type(e).__name__}: {e}"
        if _is_strict():
            raise RuntimeError(msg) from e
        _warn(msg)
        return None

    module = getattr(obj, "__module__", ep.module) or ep.module
    mod_obj = sys.modules.get(module)
    min_version = getattr(mod_obj, "MIN_API_VERSION", "1")

    if not _compare_api_version(min_version):
        msg = (
            f"plugin {ep.name!r} requires api version {min_version}, "
            f"devops provides {API_VERSION} — skipping"
        )
        if _is_strict():
            raise RuntimeError(msg)
        _warn(msg)
        return None

    baseline = len(api._registered_classes())
    classes: list[type[Target]] = []

    try:
        if isinstance(obj, type) and issubclass(obj, Target):
            api.register_target(obj)
        elif callable(obj):
            obj(api)
        else:
            msg = (
                f"plugin {ep.name!r} entry point must be a Target subclass "
                f"or a callable register(api) — got {type(obj).__name__}"
            )
            if _is_strict():
                raise TypeError(msg)
            _warn(msg)
            return None
    except Exception as e:
        msg = f"plugin {ep.name!r} register() raised: {type(e).__name__}: {e}"
        if _is_strict():
            raise RuntimeError(msg) from e
        _warn(msg)
        return None

    classes = _collect_registered_since(baseline)
    return LoadedPlugin(name=ep.name, module=module, min_api_version=min_version, classes=classes)


def load_plugins() -> list[LoadedPlugin]:
    """Discover + load every installed devops plugin. Cached."""
    global _loaded
    if _loaded is not None:
        return _loaded

    _loaded = []
    try:
        eps = _metadata.entry_points(group=ENTRY_POINT_GROUP)
    except TypeError:
        # Older importlib.metadata API on some 3.11 stdlib variants
        eps = _metadata.entry_points().get(ENTRY_POINT_GROUP, [])  # type: ignore[attr-defined]

    for ep in eps:
        result = _load_one(ep)
        if result is not None:
            _loaded.append(result)
    return _loaded


def reset_for_tests() -> None:
    """Clear the plugin cache and api-level registered classes.

    Tests that monkeypatch entry_points should call this before and
    after, or the cached result persists across test boundaries.
    """
    from devops import api

    global _loaded
    _loaded = None
    api._REGISTERED_TARGET_CLASSES.clear()
    api.DEFAULT_TOOLCHAIN_EXTRAS.clear()
