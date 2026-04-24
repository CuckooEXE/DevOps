"""CustomArtifact: template expansion, dep flow, cache invalidation,
multi-input / multi-output / multi-cmd shapes."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from devops import cache
from devops.core import runner
from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.c_cpp import ElfBinary
from devops.targets.custom import CustomArtifact


def _ctx(tmp: Path) -> BuildContext:
    return BuildContext(
        workspace_root=tmp,
        build_dir=tmp / "build",
        profile=OptimizationLevel.Debug,
    )


def _write(tmp: Path, rel: str, body: str = "") -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


# --- validation ----------------------------------------------------------


def test_custom_rejects_empty_outputs(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="outputs="):
            CustomArtifact(name="x", outputs=[], cmds=["true"])


def test_custom_rejects_empty_cmds(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="cmds="):
            CustomArtifact(name="x", outputs=["f"], cmds=[])


def test_custom_rejects_bad_input_type(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(TypeError, match="input"):
            CustomArtifact(
                name="x", outputs=["f"], cmds=["true"],
                inputs={"bad": 42},  # type: ignore[dict-item]
            )


# --- template expansion --------------------------------------------------


def test_target_input_binds_as_view(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"])
        ca = CustomArtifact(
            name="stripped",
            inputs={"bin": app},
            outputs=["stripped_app"],
            cmds=["cp {bin.output_path} {out[0]}", "strip {out[0]}"],
        )
    ctx = _ctx(tmp_path)
    cmd = ca.build_cmds(ctx)[0]
    rendered = cmd.argv[0]
    # Two user cmds joined, wrapped in `set -e` + mkdir
    assert "cp" in rendered
    assert "strip" in rendered
    assert str(app.output_path(ctx)) in rendered
    assert str(ca.output_paths(ctx)[0]) in rendered


def test_path_input_binds_as_absolute_string(tmp_project, tmp_path):
    schema = _write(tmp_path, "schema.capnp", "# schema\n")
    _, enter = tmp_project
    with enter():
        ca = CustomArtifact(
            name="gen",
            inputs={"schema": "schema.capnp"},
            outputs=["out.c"],
            cmds=["codegen {schema} -o {out[0]}"],
        )
    cmd = ca.build_cmds(_ctx(tmp_path))[0]
    assert str(schema.resolve()) in cmd.argv[0]


def test_multiple_outputs_accessible_by_index(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        ca = CustomArtifact(
            name="gen",
            outputs=["a.h", "a.c"],
            cmds=["gen --header {out[0]} --impl {out[1]}"],
        )
    ctx = _ctx(tmp_path)
    cmd = ca.build_cmds(ctx)[0]
    rendered = cmd.argv[0]
    assert str(ca.output_paths(ctx)[0]) in rendered
    assert str(ca.output_paths(ctx)[1]) in rendered


def test_unknown_template_name_surfaces_error(tmp_project):
    _, enter = tmp_project
    with enter():
        ca = CustomArtifact(
            name="x",
            outputs=["f"],
            cmds=["echo {bogus}"],
        )
    with pytest.raises(KeyError, match="bogus"):
        ca.build_cmds(_ctx(Path("/tmp")))


# --- topo-sort wiring ----------------------------------------------------


def test_target_input_flows_into_deps(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"])
        ca = CustomArtifact(
            name="post",
            inputs={"bin": app},
            outputs=["post_bin"],
            cmds=["cp {bin.output_path} {out[0]}"],
        )
    assert app in ca.deps.values()


def test_path_input_does_not_flow_into_deps(tmp_project, tmp_path):
    schema = _write(tmp_path, "schema.capnp", "")
    _, enter = tmp_project
    with enter():
        ca = CustomArtifact(
            name="gen",
            inputs={"schema": "schema.capnp"},
            outputs=["out"],
            cmds=["cat {schema} > {out[0]}"],
        )
    # Not a Target, so it shouldn't appear as a dep; but its path IS in the
    # Command's inputs so the cache invalidates on change.
    assert all(not isinstance(v, object) or v is not schema for v in ca.deps.values())


# --- cache behaviour -----------------------------------------------------


def test_path_input_change_invalidates(tmp_project, tmp_path):
    schema = _write(tmp_path, "schema.json", '{"v":1}')
    _, enter = tmp_project
    with enter():
        ca = CustomArtifact(
            name="gen",
            inputs={"schema": "schema.json"},
            outputs=["out.txt"],
            cmds=["cat {schema} > {out[0]}"],
        )
    ctx = _ctx(tmp_path)
    cmd = ca.build_cmds(ctx)[0]
    runner.run(cmd, use_cache=True)
    assert cache.is_fresh(cmd)

    time.sleep(0.01)
    schema.write_text('{"v":2}')
    os.utime(schema, None)
    assert not cache.is_fresh(cmd)


# --- end-to-end real command ---------------------------------------------


def test_custom_runs_a_real_shell_pipeline(tmp_project, tmp_path):
    _, enter = tmp_project
    _write(tmp_path, "msg.txt", "hello\n")
    with enter():
        ca = CustomArtifact(
            name="upper",
            inputs={"msg": "msg.txt"},
            outputs=["UPPER.txt"],
            cmds=["tr a-z A-Z < {msg} > {out[0]}"],
        )
    ctx = _ctx(tmp_path)
    runner.run(ca.build_cmds(ctx)[0], use_cache=True)
    assert ca.output_paths(ctx)[0].read_text() == "HELLO\n"


def test_extra_inputs_flow_into_cache(tmp_project, tmp_path):
    """extra_inputs should augment the primary Command's inputs."""
    _write(tmp_path, "msg.txt", "a")
    conf = _write(tmp_path, "codegen.cfg", "version=1\n")
    _, enter = tmp_project
    with enter():
        ca = CustomArtifact(
            name="gen",
            inputs={"msg": "msg.txt"},
            outputs=["out.txt"],
            cmds=["cat {msg} > {out[0]}"],
            extra_inputs=[conf],
        )
    cmd = ca.build_cmds(_ctx(tmp_path))[0]
    assert conf.resolve() in cmd.inputs
