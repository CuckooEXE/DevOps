"""Repo-root conftest.

Hosts shared fixtures used by both the core test suite under ``tests/``
and the plugin test suites under ``plugins/*/tests/``. Also wires each
plugin's source dir onto ``sys.path`` so plugin imports work without a
``pip install -e`` during development.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Make every plugin package importable by its distribution layout
# (plugins/<name>/<package>/). Mirrors what `pip install -e` would
# achieve but keeps the in-repo test run self-contained.
_PLUGINS_ROOT = ROOT / "plugins"
if _PLUGINS_ROOT.is_dir():
    for plugin_dir in sorted(_PLUGINS_ROOT.iterdir()):
        if plugin_dir.is_dir():
            sys.path.insert(0, str(plugin_dir))

from devops import registry  # noqa: E402
from devops.core.target import Project  # noqa: E402
from devops.targets import _specs as _target_specs  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_registry():
    registry.reset()
    _target_specs.reset_ref_prelude_dedup()
    yield
    registry.reset()
    _target_specs.reset_ref_prelude_dedup()


@pytest.fixture
def tmp_project(tmp_path: Path):
    """Yields (proj, enter_ctx) — call enter_ctx() to become the active project."""
    proj = Project(name="t", root=tmp_path)

    def enter():
        return registry.active_project(proj)

    return proj, enter
