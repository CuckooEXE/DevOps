"""Cross-compile profiles: per-arch toolchains, arch-qualified output
paths, and tests always pinned to host."""

from __future__ import annotations

from pathlib import Path

import pytest

from devops.context import (
    BuildContext,
    Tool,
    Toolchain,
    load_toolchains,
)
from devops.options import OptimizationLevel
from devops.targets.c_cpp import ElfBinary, StaticLibrary
from devops.targets.tests import GoogleTest


def _write(tmp: Path, rel: str, body: str = "") -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


# ---- BuildContext.toolchain_for -----------------------------------------


def test_toolchain_for_host_is_primary(tmp_path):
    ctx = BuildContext(workspace_root=tmp_path, build_dir=tmp_path / "build")
    assert ctx.toolchain_for("host") is ctx.toolchain


def test_toolchain_for_unknown_arch_raises_with_available_list(tmp_path):
    ctx = BuildContext(workspace_root=tmp_path, build_dir=tmp_path / "build")
    with pytest.raises(ValueError, match="aarch64.*available.*host"):
        ctx.toolchain_for("aarch64")


def test_toolchain_for_returns_per_arch_entry(tmp_path):
    aarch = Toolchain(cc=Tool.of("aarch64-gcc"))
    ctx = BuildContext(
        workspace_root=tmp_path,
        build_dir=tmp_path / "build",
        toolchains={"host": Toolchain(), "aarch64": aarch},
    )
    assert ctx.toolchain_for("aarch64") is aarch


# ---- load_toolchains (devops.toml parsing) ------------------------------


def test_load_toolchains_parses_host_and_arch_tables(tmp_path):
    (tmp_path / "devops.toml").write_text(
        "[toolchain]\n"
        "cc = 'clang'\n"
        "\n"
        "[toolchain.aarch64]\n"
        "cc = ['aarch64-linux-gnu-gcc']\n"
        "cxx = ['aarch64-linux-gnu-g++']\n"
    )
    tcs = load_toolchains(tmp_path)
    assert set(tcs.keys()) == {"host", "aarch64"}
    assert tcs["host"].cc.argv == ("clang",)
    assert tcs["aarch64"].cc.argv == ("aarch64-linux-gnu-gcc",)
    assert tcs["aarch64"].cxx.argv == ("aarch64-linux-gnu-g++",)


def test_load_toolchains_missing_toml_gives_host_default(tmp_path):
    tcs = load_toolchains(tmp_path)
    assert list(tcs.keys()) == ["host"]


# ---- output paths include arch ------------------------------------------


def test_output_dir_includes_arch_segment(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"], arch="aarch64")
    ctx = BuildContext(
        workspace_root=tmp_path,
        build_dir=tmp_path / "build",
        profile=OptimizationLevel.Debug,
        toolchains={"host": Toolchain(), "aarch64": Toolchain()},
    )
    assert "aarch64" in str(app.output_dir(ctx))
    # Distinct from a host target of the same name
    with enter():
        host_app = ElfBinary(name="app2", srcs=[tmp_path / "main.c"])
    assert "host" in str(host_app.output_dir(ctx))
    assert app.output_dir(ctx).parent != host_app.output_dir(ctx).parent


# ---- compile commands use per-arch toolchain ----------------------------


def test_cross_compile_uses_arch_toolchain(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"], arch="aarch64")
    ctx = BuildContext(
        workspace_root=tmp_path,
        build_dir=tmp_path / "build",
        profile=OptimizationLevel.Debug,
        toolchains={
            "host": Toolchain(cc=Tool.of("host-cc")),
            "aarch64": Toolchain(cc=Tool.of("aarch64-linux-gnu-gcc")),
        },
    )
    compile_cmd = app.build_cmds(ctx)[0]
    assert compile_cmd.argv[0] == "aarch64-linux-gnu-gcc"


# ---- GoogleTest always targets host even when test-target is cross -----


def test_googletest_pinned_to_host_even_when_target_is_cross(tmp_project, tmp_path):
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _write(tmp_path, "test.cc", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        lib = StaticLibrary(
            name="lib",
            srcs=[tmp_path / "lib.c"],
            arch="aarch64",
        )
        g = GoogleTest(name="t", srcs=[tmp_path / "test.cc"], target=lib)
    assert lib.arch == "aarch64"
    assert g.arch == "host"  # the key requirement

    ctx = BuildContext(
        workspace_root=tmp_path,
        build_dir=tmp_path / "build",
        profile=OptimizationLevel.Debug,
        toolchains={
            "host": Toolchain(cxx=Tool.of("host-c++")),
            "aarch64": Toolchain(cxx=Tool.of("aarch64-linux-gnu-g++")),
        },
    )
    # The test's link step uses the host cxx, not aarch64's
    link = g.build_cmds(ctx)[-1]
    assert link.argv[0] == "host-c++"


# ---- artifact default arch is host --------------------------------------


def test_artifact_default_arch_is_host(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"])
    assert app.arch == "host"
