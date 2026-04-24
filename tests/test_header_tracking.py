"""Header tracking via -MMD depfile + extra_inputs= cache invalidation."""

from __future__ import annotations

import os
import time
from pathlib import Path

from devops import cache
from devops.core import runner
from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.c_cpp import ElfBinary, StaticLibrary


def _ctx(tmp: Path) -> BuildContext:
    return BuildContext(
        workspace_root=tmp,
        build_dir=tmp / "build",
        profile=OptimizationLevel.Debug,
    )


def _write(tmp: Path, rel: str, body: str) -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


# ---- depfile parser ------------------------------------------------------


def test_depfile_parser_handles_single_line(tmp_path):
    d = tmp_path / "x.d"
    d.write_text("main.o: main.c include/header.h\n")
    deps = cache.parse_depfile(d)
    assert [str(p) for p in deps] == ["main.c", "include/header.h"]


def test_depfile_parser_handles_continuation(tmp_path):
    d = tmp_path / "x.d"
    d.write_text("main.o: main.c \\\n include/a.h \\\n include/b.h\n")
    deps = cache.parse_depfile(d)
    assert [p.name for p in deps] == ["main.c", "a.h", "b.h"]


def test_depfile_parser_ignores_missing_file(tmp_path):
    assert cache.parse_depfile(tmp_path / "nope.d") == []


def test_depfile_parser_handles_escaped_spaces(tmp_path):
    d = tmp_path / "x.d"
    d.write_text("out.o: path\\ with\\ spaces/file.c header.h\n")
    deps = cache.parse_depfile(d)
    assert [str(p) for p in deps] == ["path with spaces/file.c", "header.h"]


# ---- compile command emits -MMD -MF --------------------------------------


def test_compile_command_has_mmd_and_depfile(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        b = ElfBinary(name="x", srcs=[tmp_path / "main.c"])
    compile_cmd = b.build_cmds(_ctx(tmp_path))[0]
    assert "-MMD" in compile_cmd.argv
    assert "-MF" in compile_cmd.argv
    assert compile_cmd.depfile is not None
    assert compile_cmd.depfile.suffix == ".d"


# ---- cache invalidation when a header changes ----------------------------


def test_header_change_invalidates_cache(tmp_project, tmp_path):
    """End-to-end: compile once, touch the header, recompile should run
    (the source's mtime is unchanged)."""
    hdr = _write(tmp_path, "include/mylib.h",
                 "#pragma once\nstatic const int K = 1;\n")
    src = _write(tmp_path, "main.c",
                 '#include "mylib.h"\nint main(){return K;}\n')

    _, enter = tmp_project
    with enter():
        b = ElfBinary(
            name="x",
            srcs=[src],
            includes=[tmp_path / "include"],
        )
    ctx = _ctx(tmp_path)
    compile_cmd = b.build_cmds(ctx)[0]

    # First compile — stamp doesn't exist
    assert not cache.is_fresh(compile_cmd)
    runner.run(compile_cmd, use_cache=True)
    assert cache.is_fresh(compile_cmd)

    # Touch the header (change mtime + contents; leave the .c alone)
    time.sleep(0.01)
    hdr.write_text("#pragma once\nstatic const int K = 2;\n")
    os.utime(hdr, None)

    # Cache should now be stale because the depfile records mylib.h and
    # its mtime changed.
    assert not cache.is_fresh(compile_cmd)


def test_unrelated_header_change_does_not_invalidate(tmp_project, tmp_path):
    """If an unrelated header changes, the compile stays cached."""
    _write(tmp_path, "include/used.h", "#pragma once\n#define USED 1\n")
    unused = _write(tmp_path, "include/unused.h", "#pragma once\n")
    src = _write(
        tmp_path, "main.c",
        '#include "used.h"\nint main(){return USED;}\n',
    )

    _, enter = tmp_project
    with enter():
        b = ElfBinary(
            name="x",
            srcs=[src],
            includes=[tmp_path / "include"],
        )
    ctx = _ctx(tmp_path)
    compile_cmd = b.build_cmds(ctx)[0]
    runner.run(compile_cmd, use_cache=True)
    assert cache.is_fresh(compile_cmd)

    time.sleep(0.01)
    unused.write_text("#pragma once\n// touched\n")
    os.utime(unused, None)

    # unused.h isn't in the depfile — cache stays fresh.
    assert cache.is_fresh(compile_cmd)


# ---- extra_inputs= invalidates the link/archive step ---------------------


def test_extra_inputs_flow_into_link_step(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    ld_script = _write(tmp_path, "linker.ld", "/* fake linker script */\n")
    _, enter = tmp_project
    with enter():
        b = ElfBinary(
            name="x",
            srcs=[tmp_path / "main.c"],
            extra_inputs=[ld_script],
        )
    link_cmd = b.build_cmds(_ctx(tmp_path))[-1]
    assert ld_script.resolve() in link_cmd.inputs


def test_extra_inputs_flow_into_static_library_archive(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "int a(){return 0;}")
    schema = _write(tmp_path, "schema.capnp", "# fake schema\n")
    _, enter = tmp_project
    with enter():
        sl = StaticLibrary(
            name="mylib",
            srcs=[tmp_path / "a.c"],
            extra_inputs=[schema],
        )
    ar_cmd = sl.build_cmds(_ctx(tmp_path))[-1]
    assert schema.resolve() in ar_cmd.inputs


def test_extra_inputs_change_invalidates_link_not_compile(tmp_project, tmp_path):
    """Change an extra_input: link re-runs, compile stays cached."""
    src = _write(tmp_path, "main.c", "int main(){return 0;}")
    ld_script = _write(tmp_path, "linker.ld", "/* v1 */\n")
    _, enter = tmp_project
    with enter():
        b = ElfBinary(
            name="x",
            srcs=[src],
            extra_inputs=[ld_script],
        )
    ctx = _ctx(tmp_path)
    cmds = b.build_cmds(ctx)
    compile_cmd, link_cmd = cmds[0], cmds[-1]

    # Seed both caches
    runner.run(compile_cmd, use_cache=True)
    # Manually write link stamp so we can test staleness without actually
    # linking (which would require a real library graph).
    link_cmd.outputs[0].parent.mkdir(parents=True, exist_ok=True)
    link_cmd.outputs[0].write_bytes(b"")
    cache.write_stamp(link_cmd)
    assert cache.is_fresh(link_cmd)
    assert cache.is_fresh(compile_cmd)

    # Touch the linker script
    time.sleep(0.01)
    ld_script.write_text("/* v2 */\n")
    os.utime(ld_script, None)

    # Compile stamp unaffected; link stamp invalidated.
    assert cache.is_fresh(compile_cmd)
    assert not cache.is_fresh(link_cmd)


def test_resolve_extra_inputs_is_absolute(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _write(tmp_path, "rel/thing.txt", "x")
    _, enter = tmp_project
    with enter():
        b = ElfBinary(
            name="x",
            srcs=[tmp_path / "main.c"],
            extra_inputs=["rel/thing.txt"],
        )
    assert all(p.is_absolute() for p in b.extra_inputs)
