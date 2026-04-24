"""Tests for the TestRangeTest plugin.

Behaviour-focused: construction, env-var contract, cmd shape,
toolchain-extras override. No actual testrange or libvirt calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devops.context import BuildContext, Tool, Toolchain
from devops.options import OptimizationLevel
from devops.targets.c_cpp import ElfBinary
from devops.targets.tests import TestTarget
from devops_testrange import TestRangeTest


def _ctx(tmp_path: Path) -> BuildContext:
    # Seed the testrange tool in extras — plugin's register() would do
    # this automatically in real use, but unit tests construct ctx
    # directly without running plugin discovery.
    tc = Toolchain()
    tc.extras["testrange"] = Tool.of("testrange")
    return BuildContext(
        workspace_root=tmp_path,
        build_dir=tmp_path / "build",
        profile=OptimizationLevel.Debug,
        toolchain=tc,
    )


def _write(tmp: Path, rel: str, body: str = "") -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_testrangetest_is_a_test_target(tmp_project, tmp_path):
    _write(tmp_path, "tests/smoke.py", "def gen_tests(): return []\n")
    _, enter = tmp_project
    with enter():
        t = TestRangeTest(name="E2E", srcs=[tmp_path / "tests/smoke.py"])
    assert isinstance(t, TestTarget)


def test_build_cmds_are_empty(tmp_project, tmp_path):
    _write(tmp_path, "tests/smoke.py", "")
    _, enter = tmp_project
    with enter():
        t = TestRangeTest(name="E2E", srcs=[tmp_path / "tests/smoke.py"])
    assert t.build_cmds(_ctx(tmp_path)) == []


def test_one_command_per_src(tmp_project, tmp_path):
    _write(tmp_path, "tests/a.py", "")
    _write(tmp_path, "tests/b.py", "")
    _write(tmp_path, "tests/c.py", "")
    _, enter = tmp_project
    with enter():
        t = TestRangeTest(
            name="E2E",
            srcs=[tmp_path / "tests/a.py", tmp_path / "tests/b.py", tmp_path / "tests/c.py"],
        )
    cmds = t.test_cmds(_ctx(tmp_path))
    assert len(cmds) == 3
    assert all("testrange" in c.argv[0] for c in cmds)


def test_invokes_testrange_run_subcommand(tmp_project, tmp_path):
    """testrange's CLI is `testrange run <spec>`, not just `testrange <spec>`."""
    _write(tmp_path, "tests/smoke.py", "")
    _, enter = tmp_project
    with enter():
        t = TestRangeTest(name="E2E", srcs=[tmp_path / "tests/smoke.py"])
    cmd = t.test_cmds(_ctx(tmp_path))[0]
    assert cmd.argv[0] == "testrange"
    assert cmd.argv[1] == "run"
    assert cmd.argv[-1].endswith(":gen_tests")


def test_factory_defaults_to_gen_tests(tmp_project, tmp_path):
    _write(tmp_path, "tests/smoke.py", "")
    _, enter = tmp_project
    with enter():
        t = TestRangeTest(name="E2E", srcs=[tmp_path / "tests/smoke.py"])
    cmd = t.test_cmds(_ctx(tmp_path))[0]
    assert cmd.argv[-1].endswith(":gen_tests")


def test_factory_override(tmp_project, tmp_path):
    _write(tmp_path, "tests/smoke.py", "")
    _, enter = tmp_project
    with enter():
        t = TestRangeTest(
            name="E2E",
            srcs=[tmp_path / "tests/smoke.py"],
            factory="build_fleet",
        )
    cmd = t.test_cmds(_ctx(tmp_path))[0]
    assert cmd.argv[-1].endswith(":build_fleet")


def test_artifacts_become_env_vars(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _write(tmp_path, "tests/smoke.py", "")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="myApp", srcs=[tmp_path / "main.c"])
        t = TestRangeTest(
            name="E2E",
            srcs=[tmp_path / "tests/smoke.py"],
            artifacts={"app": app},
        )
    ctx = _ctx(tmp_path)
    cmd = t.test_cmds(ctx)[0]
    env_map = dict(cmd.env)
    assert env_map["DEVOPS_ARTIFACT_APP"] == str(app.output_path(ctx))


def test_multiple_artifacts_all_exported(tmp_project, tmp_path):
    _write(tmp_path, "a.c", "int a(){return 0;}")
    _write(tmp_path, "b.c", "int b(){return 0;}")
    _write(tmp_path, "tests/smoke.py", "")
    _, enter = tmp_project
    with enter():
        a = ElfBinary(name="A", srcs=[tmp_path / "a.c"])
        b = ElfBinary(name="B", srcs=[tmp_path / "b.c"])
        t = TestRangeTest(
            name="E2E",
            srcs=[tmp_path / "tests/smoke.py"],
            artifacts={"alpha": a, "beta": b},
        )
    env_map = dict(t.test_cmds(_ctx(tmp_path))[0].env)
    assert "DEVOPS_ARTIFACT_ALPHA" in env_map
    assert "DEVOPS_ARTIFACT_BETA" in env_map


def test_artifacts_flow_into_deps(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _write(tmp_path, "tests/smoke.py", "")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="myApp", srcs=[tmp_path / "main.c"])
        t = TestRangeTest(
            name="E2E",
            srcs=[tmp_path / "tests/smoke.py"],
            artifacts={"app": app},
        )
    assert app in t.deps.values()


def test_user_env_is_appended(tmp_project, tmp_path):
    _write(tmp_path, "tests/smoke.py", "")
    _, enter = tmp_project
    with enter():
        t = TestRangeTest(
            name="E2E",
            srcs=[tmp_path / "tests/smoke.py"],
            env={"TESTRANGE_CACHE_DIR": "/var/tmp/custom"},
        )
    env_map = dict(t.test_cmds(_ctx(tmp_path))[0].env)
    assert env_map["TESTRANGE_CACHE_DIR"] == "/var/tmp/custom"


def test_empty_artifacts_emits_no_devops_env(tmp_project, tmp_path):
    _write(tmp_path, "tests/smoke.py", "")
    _, enter = tmp_project
    with enter():
        t = TestRangeTest(name="E2E", srcs=[tmp_path / "tests/smoke.py"])
    env_map = dict(t.test_cmds(_ctx(tmp_path))[0].env)
    assert not any(k.startswith("DEVOPS_ARTIFACT_") for k in env_map)


def test_artifact_output_is_a_command_input(tmp_project, tmp_path):
    """Touching the artifact should invalidate the test command's cache
    entry — it must appear in the Command's inputs tuple."""
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _write(tmp_path, "tests/smoke.py", "")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="myApp", srcs=[tmp_path / "main.c"])
        t = TestRangeTest(
            name="E2E",
            srcs=[tmp_path / "tests/smoke.py"],
            artifacts={"app": app},
        )
    ctx = _ctx(tmp_path)
    cmd = t.test_cmds(ctx)[0]
    assert app.output_path(ctx) in cmd.inputs


def test_describe_shape(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _write(tmp_path, "tests/smoke.py", "")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="myApp", srcs=[tmp_path / "main.c"])
        t = TestRangeTest(
            name="E2E",
            srcs=[tmp_path / "tests/smoke.py"],
            artifacts={"app": app},
            factory="custom_factory",
        )
    desc = t.describe()
    assert "TestRangeTest" in desc
    assert "custom_factory" in desc
    assert "app=" in desc
    assert app.qualified_name in desc


def test_toolchain_override_via_extras(tmp_project, tmp_path):
    """A devops.toml override of [toolchain.extras] testrange must reach the Command."""
    _write(tmp_path, "tests/smoke.py", "")
    _, enter = tmp_project
    with enter():
        t = TestRangeTest(name="E2E", srcs=[tmp_path / "tests/smoke.py"])
    tc = Toolchain()
    tc.extras["testrange"] = Tool.of(["docker", "run", "--rm", "ghcr.io/acme/tr:v1", "testrange"])
    ctx = BuildContext(
        workspace_root=tmp_path,
        build_dir=tmp_path / "build",
        profile=OptimizationLevel.Debug,
        toolchain=tc,
    )
    cmd = t.test_cmds(ctx)[0]
    assert cmd.argv[0] == "docker"
    assert "ghcr.io/acme/tr:v1" in cmd.argv


def test_missing_testrange_extra_raises_friendly_error(tmp_project, tmp_path):
    """Without extras["testrange"], test_cmds raises a RuntimeError
    pointing at the plugin / devops.toml as fix paths — not a cryptic
    KeyError."""
    _write(tmp_path, "tests/smoke.py", "")
    _, enter = tmp_project
    with enter():
        t = TestRangeTest(name="E2E", srcs=[tmp_path / "tests/smoke.py"])
    ctx = BuildContext(
        workspace_root=tmp_path,
        build_dir=tmp_path / "build",
        profile=OptimizationLevel.Debug,
        toolchain=Toolchain(),
    )
    with pytest.raises(RuntimeError, match="no 'testrange' tool"):
        t.test_cmds(ctx)


def test_register_installs_class_and_tool_default():
    """register() should register TestRangeTest and seed a testrange tool."""
    from devops import api, plugins
    from devops_testrange import register

    plugins.reset_for_tests()
    try:
        register(api)
        assert TestRangeTest in api._registered_classes()
        assert "testrange" in api.DEFAULT_TOOLCHAIN_EXTRAS
    finally:
        plugins.reset_for_tests()
