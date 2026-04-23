"""Coverage for every C/C++ target type: ElfBinary, ElfSharedObject,
StaticLibrary, HeadersOnly — and the subclassing pattern."""

from __future__ import annotations

from pathlib import Path

import pytest

from devops.context import BuildContext
from devops.options import COMMON_C_FLAGS, OptimizationLevel
from devops.targets.c_cpp import (
    ElfBinary,
    ElfSharedObject,
    HeadersOnly,
    StaticLibrary,
    _resolve_sources,
)


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


def test_elfbinary_build_cmds_shape(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        b = ElfBinary(name="app", srcs=[tmp_path / "main.c"])
    cmds = b.build_cmds(_ctx(tmp_path))
    assert [c.label for c in cmds] == ["compile main.c", "link app"]
    assert cmds[-1].outputs[0].name == "app"


def test_elfsharedobject_adds_fpic_and_shared(tmp_project, tmp_path):
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _, enter = tmp_project
    with enter():
        so = ElfSharedObject(name="foo", srcs=[tmp_path / "lib.c"])
    cmds = so.build_cmds(_ctx(tmp_path))
    compile_argv = cmds[0].argv
    link_argv = cmds[-1].argv
    assert "-fPIC" in compile_argv
    assert "-shared" in link_argv
    assert cmds[-1].outputs[0].name == "libfoo.so"


def test_staticlibrary_uses_ar(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "int a(){return 0;}")
    _, enter = tmp_project
    with enter():
        lib = StaticLibrary(name="mylib", srcs=[tmp_path / "a.c"])
    cmds = lib.build_cmds(_ctx(tmp_path))
    archive_argv = cmds[-1].argv
    assert archive_argv[0].endswith("ar")
    assert archive_argv[1] == "rcs"
    assert cmds[-1].outputs[0].name == "libmylib.a"


def test_headersonly_stages_files(tmp_project, tmp_path):
    _write(tmp_path, "include/a.h", "#pragma once\n")
    _write(tmp_path, "include/sub/b.h", "#pragma once\n")
    _, enter = tmp_project
    with enter():
        h = HeadersOnly(
            name="headers",
            srcs=[tmp_path / "include/a.h", tmp_path / "include/sub/b.h"],
        )
    cmds = h.build_cmds(_ctx(tmp_path))
    stages = [c for c in cmds if c.label.startswith("stage ")]
    assert {c.label for c in stages} == {"stage a.h", "stage b.h"}


def test_rpath_embedded_when_linking_sharedobject(tmp_project, tmp_path):
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        so = ElfSharedObject(name="foo", srcs=[tmp_path / "lib.c"])
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"], libs=[so])
    ctx = _ctx(tmp_path)
    link = app.build_cmds(ctx)[-1]
    rpath_args = [a for a in link.argv if a.startswith("-Wl,-rpath,")]
    assert len(rpath_args) == 1
    assert str(so.output_path(ctx).parent) in rpath_args[0]


def test_static_library_linked_as_archive_path(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "int a(){return 0;}")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        lib = StaticLibrary(name="statlib", srcs=[tmp_path / "a.c"])
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"], libs=[lib])
    ctx = _ctx(tmp_path)
    link = app.build_cmds(ctx)[-1]
    # Static libs are passed as explicit archive paths, not -l
    assert any(a.endswith("libstatlib.a") for a in link.argv)


def test_system_lib_flows_as_dash_l(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"], libs=["ssl", "crypto"])
    link = app.build_cmds(_ctx(tmp_path))[-1]
    assert "-lssl" in link.argv
    assert "-lcrypto" in link.argv


def test_libs_flow_into_deps_for_topo_sort(tmp_project, tmp_path):
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        so = ElfSharedObject(name="foo", srcs=[tmp_path / "lib.c"])
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"], libs=[so])
    assert so in app.deps.values()


def test_glob_pattern_without_builder_glob_errors(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "")
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="glob pattern"):
            ElfBinary(name="bad", srcs=["*.c"])


def test_subclassing_bakes_flags(tmp_project, tmp_path):
    """Pattern used in the sample fixture: `class TeamBinary(ElfBinary)`
    that pins -Werror for every instance."""
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project

    class TeamBinary(ElfBinary):
        def __init__(self, **kwargs: object) -> None:
            baked = tuple(COMMON_C_FLAGS) + ("-Werror",)
            user = tuple(kwargs.pop("flags", ()) or ())  # type: ignore[arg-type]
            super().__init__(flags=baked + user, **kwargs)  # type: ignore[arg-type]

    with enter():
        b = TeamBinary(name="app", srcs=[tmp_path / "main.c"])

    flags = b._compile_flags(_ctx(tmp_path))
    assert "-Werror" in flags
    for f in COMMON_C_FLAGS:
        assert f in flags


def test_resolve_sources_rejects_missing(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        with pytest.raises(FileNotFoundError):
            _resolve_sources(tmp_path, ["does_not_exist.c"])


# ---- HeadersOnly / Ref in includes= -------------------------------------


def test_headersonly_target_in_includes_adds_minusI(tmp_project, tmp_path):
    _write(tmp_path, "include/a.h", "#pragma once\n")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        h = HeadersOnly(name="hdrs", srcs=[tmp_path / "include/a.h"])
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"], includes=[h])
    ctx = _ctx(tmp_path)
    flags = app._compile_flags(ctx)
    expected = f"-I{h.output_path(ctx)}"
    assert expected in flags


def test_headersonly_target_flows_into_deps(tmp_project, tmp_path):
    _write(tmp_path, "include/a.h", "#pragma once\n")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        h = HeadersOnly(name="hdrs", srcs=[tmp_path / "include/a.h"])
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"], includes=[h])
    assert h in app.deps.values()


def test_staticlibrary_accepts_headersonly_in_includes(tmp_project, tmp_path):
    _write(tmp_path, "include/a.h", "#pragma once\n")
    _write(tmp_path, "a.c", "int a(){return 0;}")
    _, enter = tmp_project
    with enter():
        h = HeadersOnly(name="hdrs", srcs=[tmp_path / "include/a.h"])
        lib = StaticLibrary(name="foo", srcs=[tmp_path / "a.c"], includes=[h])
    ctx = _ctx(tmp_path)
    assert f"-I{h.output_path(ctx)}" in lib._compile_flags(ctx)
    assert h in lib.deps.values()


def test_includes_mixes_paths_and_targets(tmp_project, tmp_path):
    _write(tmp_path, "include/a.h", "#pragma once\n")
    _write(tmp_path, "third_party/x.h", "#pragma once\n")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        h = HeadersOnly(name="hdrs", srcs=[tmp_path / "include/a.h"])
        app = ElfBinary(
            name="app",
            srcs=[tmp_path / "main.c"],
            includes=["third_party", h],
        )
    ctx = _ctx(tmp_path)
    flags = app._compile_flags(ctx)
    assert f"-I{(tmp_path / 'third_party').resolve()}" in flags
    assert f"-I{h.output_path(ctx)}" in flags


def test_non_headersonly_target_in_includes_raises(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "int a(){return 0;}")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        lib = StaticLibrary(name="foo", srcs=[tmp_path / "a.c"])
        app = ElfBinary(
            name="app", srcs=[tmp_path / "main.c"], includes=[lib]
        )
    with pytest.raises(TypeError, match="HeadersOnly"):
        app._compile_flags(_ctx(tmp_path))


def test_ref_in_includes_resolves_remote_headersonly(tmp_project, tmp_path):
    """A DirectoryRef pointing at a remote project whose named target is
    HeadersOnly should produce -I<staged_dir>."""
    from devops import remote
    from devops.remote import DirectoryRef

    # Isolate the on-disk cache so this test doesn't touch ~/.cache.
    remote.CACHE_ROOT = tmp_path / "remotes"
    remote._reset_for_tests()

    remote_proj = tmp_path / "vendor"
    remote_proj.mkdir()
    (remote_proj / "h.h").write_text("#pragma once\n")
    (remote_proj / "build.py").write_text(
        "from builder import HeadersOnly, glob\n"
        "HeadersOnly(name='vendhdrs', srcs=glob('h.h'))\n"
    )

    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(
            name="app",
            srcs=[tmp_path / "main.c"],
            includes=[DirectoryRef(str(remote_proj), target="vendhdrs")],
        )
    ctx = _ctx(tmp_path)
    flags = app._compile_flags(ctx)
    # -I pointing at the remote's staged include/ dir
    assert any(
        f.startswith("-I") and "remote.vendor" in f and f.endswith("/include")
        for f in flags
    )


def test_describe_renders_headersonly_include(tmp_project, tmp_path):
    _write(tmp_path, "include/a.h", "#pragma once\n")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        h = HeadersOnly(name="hdrs", srcs=[tmp_path / "include/a.h"])
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"], includes=[h])
    desc = app.describe()
    assert h.qualified_name in desc
