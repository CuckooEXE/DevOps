"""Dependency-graph export: dot / json / text.

Walks the registered Target graph (or a named subgraph) and emits it
in one of three formats. Edges come from ``Target.deps`` — the kind
encoded into the key prefix (see ``DepKind`` in ``devops.core.target``)
recovers the relation type the constructor used when injecting the
dep. Remote refs in ``libs=`` / ``includes=`` don't live in ``deps``
(they're resolved lazily), so we scan those attributes separately and
represent each ref as either an opaque ``RemoteRef`` node (default —
no network) or, with ``resolve_remotes=True``, a fully resolved
remote Target.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from devops import registry
from devops.core.target import Artifact, Target, kind_from_dep_key
from devops.remote import Ref, resolve_remote_ref

if TYPE_CHECKING:
    from devops.context import BuildContext


@dataclass
class Node:
    id: str
    name: str
    cls: str
    project: str
    output_path: str | None
    doc: str
    required_tools: tuple[str, ...]
    is_remote: bool


@dataclass
class Edge:
    src: str
    dst: str
    kind: str


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)


def _edge_kind_for(dep_key: str) -> str:
    kind = kind_from_dep_key(dep_key)
    return kind.value if kind is not None else "dep"


def _target_node(t: Target, ctx: "BuildContext | None") -> Node:
    out: str | None = None
    if ctx is not None and isinstance(t, Artifact):
        try:
            out = str(t.output_path(ctx))
        except Exception:
            out = None
    doc_first_line = t.doc.splitlines()[0] if t.doc else ""
    return Node(
        id=t.qualified_name,
        name=t.name,
        cls=type(t).__name__,
        project=t.project.name,
        output_path=out,
        doc=doc_first_line,
        required_tools=tuple(t.required_tools),
        is_remote=False,
    )


def _ref_node(ref: Ref) -> Node:
    spec = ref.to_spec()
    return Node(
        id=f"remote:{spec}",
        name=ref.target,
        cls="RemoteRef",
        project=f"remote:{spec.rsplit('::', 1)[0]}",
        output_path=None,
        doc="",
        required_tools=(),
        is_remote=True,
    )


def _iter_refs(t: Target) -> list[tuple[str, Ref]]:
    """Ref instances sitting on libs= / includes= (not in deps)."""
    out: list[tuple[str, Ref]] = []
    for attr, kind in (("libs", "lib"), ("includes", "include")):
        entries = getattr(t, attr, None)
        if not entries:
            continue
        for entry in entries:
            if isinstance(entry, Ref):
                out.append((kind, entry))
    return out


def collect(
    roots: list[Target] | None,
    *,
    ctx: "BuildContext | None" = None,
    resolve_remotes: bool = False,
) -> Graph:
    """Walk the target graph forward from ``roots`` (or all registered
    targets if None) and materialize Nodes + Edges.

    Cycles are reported in ``Graph.cycles`` rather than raised — graph
    export is a diagnostic tool, so a broken graph should still render.
    """
    g = Graph()

    if roots is None:
        roots = list(registry.all_targets())

    visited: set[int] = set()
    stack: list[Target] = []
    stack_ids: set[int] = set()

    def visit(t: Target) -> None:
        if id(t) in visited:
            return
        if id(t) in stack_ids:
            cycle = [x.qualified_name for x in stack[stack.index(t):]] + [t.qualified_name]
            g.cycles.append(cycle)
            return
        stack.append(t)
        stack_ids.add(id(t))

        node = _target_node(t, ctx)
        g.nodes[node.id] = node

        for dep_key, dep in t.deps.items():
            kind = _edge_kind_for(dep_key)
            visit(dep)
            dep_node_id = g.nodes[dep.qualified_name].id
            g.edges.append(Edge(src=dep_node_id, dst=node.id, kind=kind))

        for kind, ref in _iter_refs(t):
            if resolve_remotes:
                try:
                    resolved = resolve_remote_ref(ref)
                except Exception:
                    resolved = None
                if resolved is not None:
                    visit(resolved)
                    g.edges.append(
                        Edge(src=g.nodes[resolved.qualified_name].id, dst=node.id, kind=kind)
                    )
                    continue
            ref_n = _ref_node(ref)
            g.nodes.setdefault(ref_n.id, ref_n)
            g.edges.append(Edge(src=ref_n.id, dst=node.id, kind=kind))

        stack.pop()
        stack_ids.discard(id(t))
        visited.add(id(t))

    for r in roots:
        visit(r)

    return g


# ---------- renderers ----------


def to_json(g: Graph, *, ctx: "BuildContext | None" = None) -> str:
    profile = ctx.profile.name if ctx is not None else None
    payload = {
        "profile": profile,
        "nodes": [
            {
                "id": n.id,
                "name": n.name,
                "class": n.cls,
                "project": n.project,
                "output_path": n.output_path,
                "doc": n.doc,
                "required_tools": list(n.required_tools),
                "remote": n.is_remote,
            }
            for n in g.nodes.values()
        ],
        "edges": [{"from": e.src, "to": e.dst, "kind": e.kind} for e in g.edges],
        "cycles": g.cycles,
    }
    return json.dumps(payload, indent=2, sort_keys=False)


_DOT_COLOR_BY_CLASS = {
    "ElfBinary": "lightblue",
    "ElfSharedObject": "lightyellow",
    "StaticLibrary": "lightyellow",
    "HeadersOnly": "khaki",
    "LdBinary": "lightblue",
    "CObjectFile": "lightgrey",
    "GoogleTest": "lightpink",
    "Pytest": "lightpink",
    "TestRangeTest": "lightpink",
    "PythonWheel": "palegreen",
    "PythonApp": "palegreen",
    "PythonShiv": "palegreen",
    "SphinxDocs": "lavender",
    "ZigBinary": "lightblue",
    "ZigTest": "lightpink",
    "Script": "white",
    "Install": "gold",
    "CustomArtifact": "white",
    "RemoteRef": "grey90",
}


def _dot_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def to_dot(g: Graph) -> str:
    lines = ["digraph devops {", '  rankdir=LR;', '  node [shape=box, style="filled,rounded"];']
    for n in g.nodes.values():
        color = _DOT_COLOR_BY_CLASS.get(n.cls, "white")
        style = '"filled,rounded,dashed"' if n.is_remote else '"filled,rounded"'
        label = _dot_escape(f"{n.name}\n{n.cls}")
        lines.append(f'  "{_dot_escape(n.id)}" [label="{label}", fillcolor="{color}", style={style}];')
    for e in g.edges:
        lines.append(f'  "{_dot_escape(e.src)}" -> "{_dot_escape(e.dst)}";')
    if g.cycles:
        lines.append("  // cycles detected:")
        for c in g.cycles:
            lines.append(f"  //   {' -> '.join(c)}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def to_text(g: Graph, roots: list[Target]) -> str:
    """Indented tree rooted at each passed root; deps listed beneath."""
    out: list[str] = []
    consumer_to_deps: dict[str, list[tuple[str, str]]] = {}
    for e in g.edges:
        consumer_to_deps.setdefault(e.dst, []).append((e.src, e.kind))

    printed: set[str] = set()

    def walk(node_id: str, depth: int) -> None:
        if node_id not in g.nodes:
            return
        n = g.nodes[node_id]
        indent = "  " * depth
        marker = " (remote)" if n.is_remote else ""
        out.append(f"{indent}{n.name} ({n.cls}){marker}")
        if node_id in printed:
            return
        printed.add(node_id)
        for src_id, _kind in consumer_to_deps.get(node_id, []):
            walk(src_id, depth + 1)

    if roots:
        for r in roots:
            walk(r.qualified_name, 0)
    else:
        consumers: set[str] = {e.dst for e in g.edges}
        dep_ids: set[str] = {e.src for e in g.edges}
        top = [nid for nid in g.nodes if nid not in dep_ids or nid in consumers and nid not in dep_ids]
        if not top:
            top = list(g.nodes)
        for nid in sorted(set(top) - dep_ids) or sorted(g.nodes):
            walk(nid, 0)

    if g.cycles:
        out.append("")
        out.append("cycles:")
        for c in g.cycles:
            out.append("  " + " -> ".join(c))
    return "\n".join(out) + "\n"


def render(
    fmt: str,
    roots: list[Target] | None,
    *,
    ctx: "BuildContext | None" = None,
    resolve_remotes: bool = False,
) -> str:
    g = collect(roots, ctx=ctx, resolve_remotes=resolve_remotes)
    if fmt == "json":
        return to_json(g, ctx=ctx)
    if fmt == "dot":
        return to_dot(g)
    if fmt == "text":
        return to_text(g, roots or [])
    raise ValueError(f"unknown format: {fmt!r} (want 'dot', 'json', or 'text')")
