"""CObjectFile + LdBinary: compile/link split, linker script + map file,
cache invalidation on linker script change."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.c_cpp import CObjectFile, LdBinary, StaticLibrary


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


# --- CObjectFile ----------------------------------------------------------


def test_cobjectfile_produces_one_obj_per_source(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "int a(){return 0;}")
    _write(tmp_path, "b.c", "int b(){return 0;}")
    _, enter = tmp_project
    with enter():
        cof = CObjectFile(name="objs", srcs=[tmp_path / "a.c", tmp_path / "b.c"])
    cmds = cof.build_cmds(_ctx(tmp_path))
    assert [c.label for c in cmds] == ["compile a.c", "compile b.c"]
    # No link step
    assert len(cmds) == 2


def test_cobjectfile_object_files_paths_match_build(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "")
    _, enter = tmp_project
    with enter():
        cof = CObjectFile(name="objs", srcs=[tmp_path / "a.c"])
    ctx = _ctx(tmp_path)
    cmds = cof.build_cmds(ctx)
    assert cof.object_files(ctx) == [cmds[0].outputs[0]]


def test_cobjectfile_output_path_is_obj_dir(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "")
    _, enter = tmp_project
    with enter():
        cof = CObjectFile(name="objs", srcs=[tmp_path / "a.c"])
    assert cof.output_path(_ctx(tmp_path)).name == "obj"


def test_cobjectfile_fpic_option(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "")
    _, enter = tmp_project
    with enter():
        cof = CObjectFile(name="objs", srcs=[tmp_path / "a.c"], pic=True)
    cmd = cof.build_cmds(_ctx(tmp_path))[0]
    assert "-fPIC" in cmd.argv


def test_cobjectfile_cxx_uses_clangxx(tmp_project, tmp_path):
    _write(tmp_path, "a.cc", "int a(){return 0;}")
    _, enter = tmp_project
    with enter():
        cof = CObjectFile(name="objs", srcs=[tmp_path / "a.cc"], is_cxx=True)
    cmd = cof.build_cmds(_ctx(tmp_path))[0]
    assert cmd.argv[0].endswith("clang++")


# --- LdBinary -------------------------------------------------------------


def test_ldbinary_invokes_ld_with_objs(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "")
    _, enter = tmp_project
    with enter():
        objs = CObjectFile(name="objs", srcs=[tmp_path / "a.c"])
        b = LdBinary(name="myapp", objs=[objs])
    ctx = _ctx(tmp_path)
    cmd = b.build_cmds(ctx)[0]
    assert cmd.argv[0].endswith("ld")
    # The .o is passed positionally
    assert str(objs.object_files(ctx)[0]) in cmd.argv


def test_ldbinary_output_path_is_name(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "")
    _, enter = tmp_project
    with enter():
        objs = CObjectFile(name="objs", srcs=[tmp_path / "a.c"])
        b = LdBinary(name="myapp", objs=[objs])
    assert b.output_path(_ctx(tmp_path)).name == "myapp"


def test_ldbinary_entry_flag(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "")
    _, enter = tmp_project
    with enter():
        objs = CObjectFile(name="objs", srcs=[tmp_path / "a.c"])
        b = LdBinary(name="myapp", objs=[objs], entry="_start")
    cmd = b.build_cmds(_ctx(tmp_path))[0]
    assert "-e" in cmd.argv
    idx = list(cmd.argv).index("-e")
    assert cmd.argv[idx + 1] == "_start"


def test_ldbinary_linker_script_goes_into_inputs(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "")
    script = _write(tmp_path, "layout.ld", "/* linker script */\n")
    _, enter = tmp_project
    with enter():
        objs = CObjectFile(name="objs", srcs=[tmp_path / "a.c"])
        b = LdBinary(name="myapp", objs=[objs], linker_script="layout.ld")
    cmd = b.build_cmds(_ctx(tmp_path))[0]
    assert "-T" in cmd.argv
    assert str(script.resolve()) in cmd.argv
    assert script.resolve() in cmd.inputs


def test_ldbinary_map_file_is_declared_output(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "")
    _, enter = tmp_project
    with enter():
        objs = CObjectFile(name="objs", srcs=[tmp_path / "a.c"])
        b = LdBinary(name="myapp", objs=[objs], map_file="myapp.map")
    ctx = _ctx(tmp_path)
    cmd = b.build_cmds(ctx)[0]
    map_path = b.map_path(ctx)
    assert map_path is not None
    assert map_path in cmd.outputs
    assert "-Map" in cmd.argv


def test_ldbinary_extra_ld_flags_appear_verbatim(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "")
    _, enter = tmp_project
    with enter():
        objs = CObjectFile(name="objs", srcs=[tmp_path / "a.c"])
        b = LdBinary(name="x", objs=[objs], extra_ld_flags=("-nostdlib", "--gc-sections"))
    cmd = b.build_cmds(_ctx(tmp_path))[0]
    assert "-nostdlib" in cmd.argv
    assert "--gc-sections" in cmd.argv


def test_ldbinary_static_archive_linked_as_path(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "")
    _write(tmp_path, "lib.c", "")
    _, enter = tmp_project
    with enter():
        objs = CObjectFile(name="objs", srcs=[tmp_path / "a.c"])
        lib = StaticLibrary(name="deps", srcs=[tmp_path / "lib.c"])
        b = LdBinary(name="x", objs=[objs], libs=[lib])
    ctx = _ctx(tmp_path)
    cmd = b.build_cmds(ctx)[0]
    assert any(a.endswith("libdeps.a") for a in cmd.argv)


def test_ldbinary_string_lib_becomes_dash_l(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "")
    _, enter = tmp_project
    with enter():
        objs = CObjectFile(name="objs", srcs=[tmp_path / "a.c"])
        b = LdBinary(name="x", objs=[objs], libs=["gcc"])
    cmd = b.build_cmds(_ctx(tmp_path))[0]
    assert "-lgcc" in cmd.argv


def test_ldbinary_literal_flag_in_objs_not_treated_as_path(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "")
    _, enter = tmp_project
    with enter():
        objs = CObjectFile(name="objs", srcs=[tmp_path / "a.c"])
        b = LdBinary(
            name="x",
            objs=[objs, "--whole-archive", "--no-whole-archive"],
        )
    cmd = b.build_cmds(_ctx(tmp_path))[0]
    assert "--whole-archive" in cmd.argv
    assert "--no-whole-archive" in cmd.argv


def test_ldbinary_targets_flow_into_deps(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "")
    _write(tmp_path, "lib.c", "")
    _, enter = tmp_project
    with enter():
        objs = CObjectFile(name="objs", srcs=[tmp_path / "a.c"])
        lib = StaticLibrary(name="deps", srcs=[tmp_path / "lib.c"])
        b = LdBinary(name="x", objs=[objs], libs=[lib])
    assert objs in b.deps.values()
    assert lib in b.deps.values()


def test_ldbinary_linker_script_change_invalidates_cache(tmp_project, tmp_path):
    """Touch the linker script; the ld step's stamp should go stale."""
    from devops import cache

    _write(tmp_path, "a.c", "")
    script = _write(tmp_path, "layout.ld", "/* v1 */\n")
    _, enter = tmp_project
    with enter():
        objs = CObjectFile(name="objs", srcs=[tmp_path / "a.c"])
        b = LdBinary(name="x", objs=[objs], linker_script="layout.ld")
    ctx = _ctx(tmp_path)
    ld_cmd = b.build_cmds(ctx)[0]

    # Seed the stamp without actually running ld (inputs + output file
    # hand-stub so is_fresh reads consistent state).
    ld_cmd.outputs[0].parent.mkdir(parents=True, exist_ok=True)
    for o in ld_cmd.outputs:
        o.write_bytes(b"")
    cache.write_stamp(ld_cmd)
    assert cache.is_fresh(ld_cmd)

    time.sleep(0.01)
    script.write_text("/* v2 */\n")
    os.utime(script, None)

    assert not cache.is_fresh(ld_cmd)
