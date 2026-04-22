"""Dependency graph: topo sort + cycle detection."""

from __future__ import annotations

from devops.core.target import Target


def topo_order(roots: list[Target]) -> list[Target]:
    """Return roots and all transitive deps in dependency order (deps first).

    Raises ValueError on cycle, naming the cycle.
    """
    visited: set[int] = set()
    in_progress: list[Target] = []
    ordered: list[Target] = []

    def visit(t: Target) -> None:
        if id(t) in visited:
            return
        if t in in_progress:
            cycle_names = " -> ".join(x.qualified_name for x in in_progress[in_progress.index(t):] + [t])
            raise ValueError(f"dependency cycle: {cycle_names}")
        in_progress.append(t)
        for dep in t.deps.values():
            visit(dep)
        in_progress.pop()
        visited.add(id(t))
        ordered.append(t)

    for r in roots:
        visit(r)
    return ordered
