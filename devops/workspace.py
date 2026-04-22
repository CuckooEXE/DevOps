"""Workspace root detection + build.py discovery."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from devops import registry
from devops.core.target import Project


WORKSPACE_MARKER = "devops.toml"


def find_workspace_root(start: Path) -> Path:
    """Walk up from `start` to find a dir containing devops.toml, else .git.

    If nothing matches, treat `start` itself as the root. Keeps the tool
    usable in directories without a workspace config.
    """
    cur = start.resolve()
    git_fallback: Path | None = None
    for candidate in (cur, *cur.parents):
        if (candidate / WORKSPACE_MARKER).is_file():
            return candidate
        if git_fallback is None and (candidate / ".git").exists():
            git_fallback = candidate
    return git_fallback or start.resolve()


def _iter_build_files(root: Path) -> list[Path]:
    # Don't descend into build/, .git/, node_modules/, __pycache__/, venvs
    skip = {"build", ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}
    out: list[Path] = []
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except (PermissionError, FileNotFoundError):
            continue
        for e in entries:
            if e.is_dir():
                if e.name in skip:
                    continue
                stack.append(e)
            elif e.name == "build.py":
                out.append(e)
    return out


def _load_build_py(path: Path, project: Project) -> None:
    """exec a build.py with `project` as the active registry context."""
    module_name = f"devops._build_{project.name}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    with registry.active_project(project):
        spec.loader.exec_module(module)


def discover_projects(workspace_root: Path) -> list[Project]:
    """Find every build.py under workspace_root, import each with its project.

    Side effect: populates the target registry.
    """
    registry.reset()
    projects: list[Project] = []
    for bp in sorted(_iter_build_files(workspace_root)):
        proj_root = bp.parent
        # Project name: path relative to workspace root, slashes→dots.
        # Root-level build.py takes the workspace dir name.
        try:
            rel = proj_root.relative_to(workspace_root)
        except ValueError:
            rel = Path(proj_root.name)
        name = str(rel).replace("/", ".") if str(rel) != "." else workspace_root.name
        proj = Project(name=name, root=proj_root)
        projects.append(proj)
        _load_build_py(bp, proj)
    return projects
