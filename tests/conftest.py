"""Shared pytest helpers — resets registry between tests, spins ephemeral projects."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from devops import registry  # noqa: E402
from devops.core.target import Project  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_registry():
    registry.reset()
    yield
    registry.reset()


@pytest.fixture
def tmp_project(tmp_path: Path):
    """Yields (proj, enter_ctx) — call enter_ctx() to become the active project."""
    proj = Project(name="t", root=tmp_path)

    def enter():
        return registry.active_project(proj)

    return proj, enter
