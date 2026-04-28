"""devops graph — dot/json/text export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devops import graph_export
from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.remote import DirectoryRef
from devops.targets.c_cpp import ElfBinary, HeadersOnly, StaticLibrary


def _ctx(tmp_path: Path) -> BuildContext:
    return BuildContext(
        workspace_root=tmp_path,
        build_dir=tmp_path / "build",
        profile=OptimizationLevel.Debug,
    )


def _write(tmp: Path, rel: str, body: str = "") -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_json_includes_every_reachable_target(tmp_project, tmp_path):
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        lib = StaticLibrary(name="mylib", srcs=[tmp_path / "lib.c"])
        app = ElfBinary(name="myApp", srcs=[tmp_path / "main.c"], libs=[lib])
    out = graph_export.render("json", [app], ctx=_ctx(tmp_path))
    data = json.loads(out)
    names = {n["name"] for n in data["nodes"]}
    assert "myApp" in names
    assert "mylib" in names


def test_json_classifies_lib_edge(tmp_project, tmp_path):
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        lib = StaticLibrary(name="mylib", srcs=[tmp_path / "lib.c"])
        app = ElfBinary(name="myApp", srcs=[tmp_path / "main.c"], libs=[lib])
    data = json.loads(graph_export.render("json", [app], ctx=_ctx(tmp_path)))
    edges = data["edges"]
    assert any(e["kind"] == "lib" for e in edges), f"no lib edge in {edges}"


def test_json_classifies_include_edge(tmp_project, tmp_path):
    _write(tmp_path, "include/a.h", "#pragma once\n")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        hdrs = HeadersOnly(name="hdrs", srcs=[tmp_path / "include/a.h"])
        app = ElfBinary(name="myApp", srcs=[tmp_path / "main.c"], includes=[hdrs])
    data = json.loads(graph_export.render("json", [app], ctx=_ctx(tmp_path)))
    assert any(e["kind"] == "include" for e in data["edges"])


def test_json_classifies_archive_and_copy_edges(tmp_project, tmp_path):
    """Previously-unrecognized prefixes (_arc_, _src_) now flow through
    DepKind so graph_export labels them with their real kind instead of
    the generic ``"dep"`` fallback."""
    from devops.targets.archive import CompressedArtifact, CompressionFormat
    from devops.targets.copy import FileArtifact

    _write(tmp_path, "data/a.txt", "alpha")
    _write(tmp_path, "msg.txt", "hello")
    _, enter = tmp_project
    with enter():
        upstream = FileArtifact(name="copied", src="msg.txt")
        bundle = CompressedArtifact(
            name="bundle",
            format=CompressionFormat.TarGzip,
            entries={"bin/copied.txt": upstream, "share/data": "data"},
        )
    data = json.loads(graph_export.render("json", [bundle], ctx=_ctx(tmp_path)))
    edge_kinds = {e["kind"] for e in data["edges"]}
    assert "archive" in edge_kinds


def test_json_classifies_input_edge(tmp_project, tmp_path):
    """CustomArtifact ``inputs={...: target}`` gets the ``input`` kind."""
    from devops.targets.custom import CustomArtifact

    _write(tmp_path, "src.txt", "x")
    _, enter = tmp_project
    with enter():
        upstream = CustomArtifact(
            name="up",
            inputs={"src": "src.txt"},
            outputs=["up.out"],
            cmds=["cp {src} {out[0]}"],
        )
        downstream = CustomArtifact(
            name="dn",
            inputs={"up": upstream},
            outputs=["dn.out"],
            cmds=["cp {up.output_path} {out[0]}"],
        )
    data = json.loads(graph_export.render("json", [downstream], ctx=_ctx(tmp_path)))
    edge_kinds = {e["kind"] for e in data["edges"]}
    assert "input" in edge_kinds


def test_dot_renders_nodes_and_edges(tmp_project, tmp_path):
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        lib = StaticLibrary(name="mylib", srcs=[tmp_path / "lib.c"])
        app = ElfBinary(name="myApp", srcs=[tmp_path / "main.c"], libs=[lib])
    out = graph_export.render("dot", [app], ctx=_ctx(tmp_path))
    assert out.startswith("digraph devops {")
    assert "myApp" in out
    assert "mylib" in out
    assert "->" in out


def test_text_renders_tree(tmp_project, tmp_path):
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        lib = StaticLibrary(name="mylib", srcs=[tmp_path / "lib.c"])
        app = ElfBinary(name="myApp", srcs=[tmp_path / "main.c"], libs=[lib])
    out = graph_export.render("text", [app], ctx=_ctx(tmp_path))
    assert "myApp" in out
    assert "mylib" in out


def test_subgraph_excludes_unrelated(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "int a(){return 0;}")
    _write(tmp_path, "b.c", "int b(){return 0;}")
    _, enter = tmp_project
    with enter():
        a = ElfBinary(name="aApp", srcs=[tmp_path / "a.c"])
        _b = ElfBinary(name="bApp", srcs=[tmp_path / "b.c"])  # unrelated
    data = json.loads(graph_export.render("json", [a], ctx=_ctx(tmp_path)))
    names = {n["name"] for n in data["nodes"]}
    assert "aApp" in names
    assert "bApp" not in names


def test_opaque_remote_ref_no_network(tmp_project, tmp_path):
    """A DirectoryRef in libs= appears as a RemoteRef node by default, no fetch."""
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        ref = DirectoryRef(path="/nonexistent/path", target="Faux")
        app = ElfBinary(name="myApp", srcs=[tmp_path / "main.c"], libs=[ref])
    data = json.loads(graph_export.render("json", [app], ctx=_ctx(tmp_path)))
    remote_nodes = [n for n in data["nodes"] if n["remote"]]
    assert len(remote_nodes) == 1
    assert remote_nodes[0]["class"] == "RemoteRef"


def test_no_roots_dumps_whole_registry(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "int a(){return 0;}")
    _write(tmp_path, "b.c", "int b(){return 0;}")
    _, enter = tmp_project
    with enter():
        ElfBinary(name="aApp", srcs=[tmp_path / "a.c"])
        ElfBinary(name="bApp", srcs=[tmp_path / "b.c"])
    data = json.loads(graph_export.render("json", None, ctx=_ctx(tmp_path)))
    names = {n["name"] for n in data["nodes"]}
    assert names >= {"aApp", "bApp"}


def test_profile_in_json_metadata(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        ElfBinary(name="aApp", srcs=[tmp_path / "a.c"])
    data = json.loads(graph_export.render("json", None, ctx=_ctx(tmp_path)))
    assert data["profile"] == "Debug"


def test_unknown_format_raises(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        ElfBinary(name="aApp", srcs=[tmp_path / "a.c"])
    with pytest.raises(ValueError, match="unknown format"):
        graph_export.render("svg", None, ctx=_ctx(tmp_path))


def test_output_path_absent_for_non_artifact(tmp_project, tmp_path):
    """Script has no output_path — JSON should still render it without error."""
    from devops.core.target import Script

    _, enter = tmp_project
    with enter():
        s = Script(name="mk", cmds=["echo hi"])
    data = json.loads(graph_export.render("json", [s], ctx=_ctx(tmp_path)))
    scripts = [n for n in data["nodes"] if n["name"] == "mk"]
    assert len(scripts) == 1
    assert scripts[0]["output_path"] is None


def test_dot_escapes_quotes_and_newlines():
    g = graph_export.Graph()
    g.nodes["x"] = graph_export.Node(
        id='weird"id', name='na\nme', cls="X", project="p",
        output_path=None, doc="", required_tools=(), is_remote=False,
    )
    out = graph_export.to_dot(g)
    assert '\\"' in out
    assert "\\n" in out
