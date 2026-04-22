"""Script target behaviour: inline cmds w/ deps templating, script=file form,
validation rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from devops.context import BuildContext
from devops.core.target import Script
from devops.options import OptimizationLevel
from devops.targets.c_cpp import ElfBinary


def _ctx(tmp: Path) -> BuildContext:
    return BuildContext(workspace_root=tmp, build_dir=tmp / "build", profile=OptimizationLevel.Debug)


def test_script_requires_exactly_one_of_cmds_or_script(tmp_project):
    _, enter = tmp_project
    with enter():
        with pytest.raises(ValueError, match="exactly one"):
            Script(name="a")
        with pytest.raises(ValueError, match="exactly one"):
            Script(name="a", cmds=["echo x"], script="run.sh")


def test_script_file_form_invokes_bash(tmp_project, tmp_path):
    (tmp_path / "run.sh").write_text("#!/usr/bin/env bash\necho hi\n")
    _, enter = tmp_project
    with enter():
        s = Script(name="s", script="run.sh")
    cmd = s.run_cmds(_ctx(tmp_path))[0]
    assert cmd.argv[0] == "bash"
    assert cmd.argv[1].endswith("run.sh")


def test_script_inline_multi_dep_templating(tmp_project, tmp_path):
    (tmp_path / "a.c").write_text("int a(){return 0;}")
    (tmp_path / "b.c").write_text("int b(){return 0;}")
    _, enter = tmp_project
    with enter():
        a = ElfBinary(name="a", srcs=[tmp_path / "a.c"])
        b = ElfBinary(name="b", srcs=[tmp_path / "b.c"])
        s = Script(
            name="push",
            deps={"x": a, "y": b},
            cmds=["scp {x.output_path} {y.output_path} host:/tmp/"],
        )
    rendered = s.run_cmds(_ctx(tmp_path))[0].argv[0]
    assert str(a.output_path(_ctx(tmp_path))) in rendered
    assert str(b.output_path(_ctx(tmp_path))) in rendered


def test_script_template_name_attribute(tmp_project, tmp_path):
    (tmp_path / "a.c").write_text("int a(){return 0;}")
    _, enter = tmp_project
    with enter():
        a = ElfBinary(name="MyApp", srcs=[tmp_path / "a.c"])
        s = Script(name="push", deps={"app": a}, cmds=["echo {app.name}"])
    assert s.run_cmds(_ctx(tmp_path))[0].argv[0] == "echo MyApp"


def test_script_rejects_unknown_template_attr(tmp_project, tmp_path):
    (tmp_path / "a.c").write_text("int a(){return 0;}")
    _, enter = tmp_project
    with enter():
        a = ElfBinary(name="a", srcs=[tmp_path / "a.c"])
        s = Script(name="bad", deps={"a": a}, cmds=["echo {a.nonsense}"])
    with pytest.raises(AttributeError, match="no template attribute 'nonsense'"):
        s.run_cmds(_ctx(tmp_path))


def test_script_describe_shows_script_or_cmd_count(tmp_project, tmp_path):
    (tmp_path / "run.sh").write_text("")
    _, enter = tmp_project
    with enter():
        s1 = Script(name="s1", cmds=["a", "b", "c"])
        s2 = Script(name="s2", script="run.sh")
    assert "3 cmd" in s1.describe()
    assert "run.sh" in s2.describe()
