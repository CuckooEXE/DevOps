"""GoogleTest / Pytest behaviour: flag inheritance, libs inheritance,
and the tests= sugar that desugars onto Artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest

from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.c_cpp import ElfBinary, ElfSharedObject, StaticLibrary
from devops.targets.python import PythonWheel
from devops.targets.tests import GoogleTest, Pytest


def _ctx(tmp: Path) -> BuildContext:
    return BuildContext(workspace_root=tmp, build_dir=tmp / "build", profile=OptimizationLevel.Debug)


def _write(tmp: Path, rel: str, body: str = "") -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_googletest_inherits_flags_from_target(tmp_project, tmp_path):
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _write(tmp_path, "test.cc", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        lib = StaticLibrary(
            name="lib",
            srcs=[tmp_path / "lib.c"],
            includes=[tmp_path / "lib.c"],
            flags=("-Wall",),
            defs={"FOO": None},
        )
        g = GoogleTest(name="t", srcs=[tmp_path / "test.cc"], target=lib)
    assert "-Wall" in g.flags
    assert "FOO" in g.defs


def test_googletest_links_library_target(tmp_project, tmp_path):
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _write(tmp_path, "test.cc", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        lib = StaticLibrary(name="lib", srcs=[tmp_path / "lib.c"])
        g = GoogleTest(name="t", srcs=[tmp_path / "test.cc"], target=lib)
    assert lib in g.libs


def test_googletest_inherits_libs_when_target_is_binary(tmp_project, tmp_path):
    """A GoogleTest whose target is an ElfBinary should link everything the
    binary links, so tests see the same library environment."""
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _write(tmp_path, "test.cc", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        so = ElfSharedObject(name="alib", srcs=[tmp_path / "lib.c"])
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"], libs=[so])
        g = GoogleTest(name="t", srcs=[tmp_path / "test.cc"], target=app)
    assert so in g.libs


def test_googletest_rejects_non_ccompile_target(tmp_project, tmp_path):
    _write(tmp_path, "test.cc", "")
    _, enter = tmp_project
    with enter():
        wheel = PythonWheel(name="w", srcs=None)
        with pytest.raises(TypeError, match="CCompile"):
            GoogleTest(name="t", srcs=[tmp_path / "test.cc"], target=wheel)


def test_googletest_implicit_dep_on_target(tmp_project, tmp_path):
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _write(tmp_path, "test.cc", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        lib = StaticLibrary(name="lib", srcs=[tmp_path / "lib.c"])
        g = GoogleTest(name="t", srcs=[tmp_path / "test.cc"], target=lib)
    assert lib in g.deps.values()


def test_elfbinary_tests_kwarg_desugars_to_googletest(tmp_project, tmp_path):
    from devops import registry

    _write(tmp_path, "main.c", "int main(){return 0;}")
    _write(tmp_path, "test.cc", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        ElfBinary(
            name="app",
            srcs=[tmp_path / "main.c"],
            tests={"srcs": [tmp_path / "test.cc"]},
        )
    names = {t.name for t in registry.all_targets()}
    assert "app" in names
    assert "appTests" in names
    # and it's a GoogleTest specifically
    test_target = next(t for t in registry.all_targets() if t.name == "appTests")
    assert isinstance(test_target, GoogleTest)


def test_pythonwheel_tests_kwarg_desugars_to_pytest(tmp_project, tmp_path):
    from devops import registry

    _write(tmp_path, "pyproject.toml", "[project]\nname='x'\nversion='0'\n")
    _write(tmp_path, "test_x.py", "def test_ok(): assert True\n")
    _, enter = tmp_project
    with enter():
        PythonWheel(
            name="x",
            tests={"srcs": [tmp_path / "test_x.py"]},
        )
    names = {t.name for t in registry.all_targets()}
    assert "xTests" in names
    pt = next(t for t in registry.all_targets() if t.name == "xTests")
    assert isinstance(pt, Pytest)


def test_pytest_sets_pythonpath_to_wheel_dir(tmp_project, tmp_path):
    _write(tmp_path, "pytools/pyproject.toml", "[project]\nname='pkg'\nversion='0'\n")
    _write(tmp_path, "pytools/tests/test_x.py", "def test_ok(): assert True\n")
    _, enter = tmp_project
    with enter():
        wheel = PythonWheel(name="pkg", pyproject="pytools/pyproject.toml")
        pt = Pytest(
            name="pkgTests",
            srcs=[tmp_path / "pytools/tests/test_x.py"],
            target=wheel,
        )
    cmds = pt.test_cmds(_ctx(tmp_path))
    env = dict(cmds[0].env)
    assert "PYTHONPATH" in env
    assert env["PYTHONPATH"].endswith("pytools")
