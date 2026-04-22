"""Process-global target registry populated by importing build.py files.

Flow:
    workspace.discover_projects() walks the tree and for each Project
    calls _enter_project(proj), exec()s its build.py, _exit_project().
    Inside that window, any Target.__init__ registers itself under the
    active Project.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devops.core.target import Project, Target


_active_project: "Project | None" = None
_all_targets: list["Target"] = []


def _enter_project(project: "Project") -> None:
    global _active_project
    if _active_project is not None:
        raise RuntimeError(
            f"cannot enter project {project.name!r} while {_active_project.name!r} is active"
        )
    _active_project = project


def _exit_project() -> None:
    global _active_project
    _active_project = None


@contextlib.contextmanager
def active_project(project: "Project"):
    _enter_project(project)
    try:
        yield project
    finally:
        _exit_project()


def current_project() -> "Project":
    if _active_project is None:
        raise RuntimeError(
            "no active project — Targets can only be constructed while a build.py is being imported"
        )
    return _active_project


def register(target: "Target") -> None:
    _all_targets.append(target)


def all_targets() -> list["Target"]:
    return list(_all_targets)


def reset() -> None:
    """Testing hook: clear registry state."""
    global _active_project, _all_targets
    _active_project = None
    _all_targets = []


def resolve(name: str, current: "Project | None" = None) -> "Target":
    """Resolve a name spec to exactly one target.

    Accepts:
        MyTarget                 unique across workspace or error
        project::MyTarget        qualified
        ::MyTarget               same project as `current`
    """
    if "::" in name:
        proj_part, target_part = name.split("::", 1)
        if not proj_part:
            if current is None:
                raise ValueError(f"'::{target_part}' requires a current project")
            candidates = [t for t in _all_targets if t.project is current and t.name == target_part]
        else:
            candidates = [t for t in _all_targets if t.project.name == proj_part and t.name == target_part]
    else:
        candidates = [t for t in _all_targets if t.name == name]

    if len(candidates) == 0:
        raise LookupError(f"no target matches {name!r}")
    if len(candidates) > 1:
        qn = ", ".join(t.qualified_name for t in candidates)
        raise LookupError(f"{name!r} is ambiguous; try a qualified name. Matches: {qn}")
    return candidates[0]
