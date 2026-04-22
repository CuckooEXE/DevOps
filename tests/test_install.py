"""Install target: command shapes per artifact type + validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.c_cpp import (
    ElfBinary,
    ElfSharedObject,
    HeadersOnly,
    StaticLibrary,
)
from devops.targets.install import Install
from devops.targets.python import PythonWheel
from devops.targets.script import Script


def _ctx(tmp: Path) -> BuildContext:
    return BuildContext(workspace_root=tmp, build_dir=tmp / "build", profile=OptimizationLevel.Debug)


def _seed(tmp: Path, rel: str, body: str = "") -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_install_rejects_non_artifact(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        s = Script(name="s", cmds=["true"])
        with pytest.raises(TypeError, match="Artifact"):
            Install(name="i", artifact=s, dest="/tmp")  # type: ignore[arg-type]


def test_install_requires_dest_for_non_wheel(tmp_project, tmp_path):
    _seed(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"])
        with pytest.raises(ValueError, match="dest= required"):
            Install(name="i", artifact=app)


def test_install_elfbinary_uses_install_dash_D(tmp_project, tmp_path):
    _seed(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"])
        inst = Install(name="i", artifact=app, dest="/opt/bin", mode="0755")
    ctx = _ctx(tmp_path)
    cmd = inst.install_cmds(ctx)[0]
    assert cmd.argv[0] == "install"
    assert "-m" in cmd.argv and "0755" in cmd.argv
    assert "-D" in cmd.argv
    assert cmd.argv[-1] == "/opt/bin/app"


def test_install_sharedobject_uses_lib_prefix(tmp_project, tmp_path):
    _seed(tmp_path, "lib.c", "int f(){return 0;}")
    _, enter = tmp_project
    with enter():
        so = ElfSharedObject(name="foo", srcs=[tmp_path / "lib.c"])
        inst = Install(name="i", artifact=so, dest="/opt/lib")
    cmd = inst.install_cmds(_ctx(tmp_path))[0]
    assert cmd.argv[-1] == "/opt/lib/libfoo.so"


def test_install_staticlibrary_uses_dot_a(tmp_project, tmp_path):
    _seed(tmp_path, "lib.c", "int f(){return 0;}")
    _, enter = tmp_project
    with enter():
        sl = StaticLibrary(name="foo", srcs=[tmp_path / "lib.c"])
        inst = Install(name="i", artifact=sl, dest="/opt/lib")
    cmd = inst.install_cmds(_ctx(tmp_path))[0]
    assert cmd.argv[-1] == "/opt/lib/libfoo.a"


def test_install_headers_issues_mkdir_and_cp(tmp_project, tmp_path):
    _seed(tmp_path, "include/a.h", "")
    _, enter = tmp_project
    with enter():
        h = HeadersOnly(name="h", srcs=[tmp_path / "include/a.h"])
        inst = Install(name="i", artifact=h, dest="/opt/include")
    cmds = inst.install_cmds(_ctx(tmp_path))
    labels = [c.label for c in cmds]
    assert any("mkdir" in l for l in labels)
    assert any("install headers" in l for l in labels)


def test_install_sudo_prefixes_install(tmp_project, tmp_path):
    _seed(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"])
        inst = Install(name="i", artifact=app, dest="/usr/local/bin", sudo=True)
    cmd = inst.install_cmds(_ctx(tmp_path))[0]
    assert cmd.argv[0] == "sudo"
    assert cmd.argv[1] == "install"


def test_install_pythonwheel_emits_pip_install(tmp_project, tmp_path):
    _seed(tmp_path, "pyproject.toml", "[project]\nname='p'\nversion='0'\n")
    _, enter = tmp_project
    with enter():
        wheel = PythonWheel(name="p")
        inst = Install(name="i", artifact=wheel, pip_args=("--user",))
    cmd = inst.install_cmds(_ctx(tmp_path))[0]
    assert cmd.shell  # uses a shell so *.whl glob expands at run time
    rendered = cmd.argv[0]
    assert "-m pip install" in rendered
    assert "--user" in rendered
    assert rendered.endswith("/*.whl")


def test_install_registers_artifact_as_dep(tmp_project, tmp_path):
    _seed(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"])
        inst = Install(name="i", artifact=app, dest="/opt/bin")
    assert app in inst.deps.values()
