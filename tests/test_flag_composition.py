from pathlib import Path

from devops.context import BuildContext, Tool, Toolchain
from devops.options import COMMON_C_FLAGS, OptimizationLevel
from devops.targets.c_cpp import ElfBinary


def _seed_project(tmp_path: Path):
    (tmp_path / "main.c").write_text("int main(){return 0;}")
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "foo.h").write_text("")


def test_compile_flags_golden(tmp_project, tmp_path):
    _seed_project(tmp_path)
    proj, enter = tmp_project
    with enter():
        b = ElfBinary(
            name="app",
            srcs=[tmp_path / "main.c"],
            includes=["include"],
            flags=COMMON_C_FLAGS,
            defs={"FOO": None, "BAR": "baz"},
            undefs=["QUX"],
        )

    ctx = BuildContext(workspace_root=tmp_path, build_dir=tmp_path / "build", profile=OptimizationLevel.Debug)
    flags = b._compile_flags(ctx)
    assert flags == (
        "-O0", "-ggdb", "-DDEBUG",
        f"-I{tmp_path/'include'}",
        "-DFOO", "-DBAR=baz",
        "-UQUX",
        "-Wall", "-Wextra", "-Wpedantic", "-fno-common", "-fstrict-aliasing",
    )


def test_lint_reuses_compile_flags(tmp_project, tmp_path):
    _seed_project(tmp_path)
    _, enter = tmp_project
    with enter():
        b = ElfBinary(name="app", srcs=[tmp_path / "main.c"], includes=["include"], defs={"FOO": None})

    ctx = BuildContext(workspace_root=tmp_path, build_dir=tmp_path / "build", profile=OptimizationLevel.Debug)
    lint = b.lint_cmds(ctx)
    # clang-tidy command: ['clang-tidy', <src>, '--', <compile_flags...>]
    tidy = next(c for c in lint if c.argv[0].endswith("clang-tidy"))
    dash_idx = tidy.argv.index("--")
    flags_to_tidy = tidy.argv[dash_idx + 1:]
    assert flags_to_tidy == b._compile_flags(ctx)


def test_docker_toolchain_wraps_compile(tmp_project, tmp_path):
    _seed_project(tmp_path)
    _, enter = tmp_project
    with enter():
        b = ElfBinary(name="app", srcs=[tmp_path / "main.c"], includes=["include"])

    docker_cc = Tool.of(["docker", "run", "-v", "{workspace}:{workspace}", "-w", "{cwd}", "cc:v1"])
    ctx = BuildContext(
        workspace_root=tmp_path,
        build_dir=tmp_path / "build",
        profile=OptimizationLevel.Debug,
        toolchain=Toolchain(cc=docker_cc),
    )
    cmds = b.build_cmds(ctx)
    compile_cmd = cmds[0]
    assert compile_cmd.argv[:7] == (
        "docker", "run",
        "-v", f"{tmp_path}:{tmp_path}",
        "-w", str(tmp_path),
        "cc:v1",
    )
