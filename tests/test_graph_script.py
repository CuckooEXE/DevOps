import pytest

from devops.context import BuildContext
from devops.core.target import Script
from devops.graph import topo_order
from devops.options import OptimizationLevel
from devops.targets.c_cpp import ElfBinary


def test_topo_order_puts_deps_first(tmp_project, tmp_path):
    (tmp_path / "a.c").write_text("int a(){return 0;}")
    (tmp_path / "b.c").write_text("int b(){return 0;}")
    _, enter = tmp_project
    with enter():
        a = ElfBinary(name="a", srcs=[tmp_path / "a.c"])
        b = ElfBinary(name="b", srcs=[tmp_path / "b.c"], deps={"lib": a})

    order = topo_order([b])
    assert order.index(a) < order.index(b)


def test_cycle_detected(tmp_project, tmp_path):
    (tmp_path / "a.c").write_text("int a(){return 0;}")
    _, enter = tmp_project
    with enter():
        a = ElfBinary(name="a", srcs=[tmp_path / "a.c"])
        s = Script(name="s", cmds=["echo hi"], deps={"a": a})
        # Manually create a cycle: add s as a dep of a
        a.deps["s"] = s

    with pytest.raises(ValueError, match="dependency cycle"):
        topo_order([a])


def test_script_template_expands_output_path_and_name(tmp_project, tmp_path):
    (tmp_path / "main.c").write_text("int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="MyApp", srcs=[tmp_path / "main.c"])
        s = Script(
            name="push",
            deps={"app": app},
            cmds=[
                "scp {app.output_path} user@host:/tmp/",
                "ssh user@host /tmp/{app.name}",
            ],
        )

    ctx = BuildContext(workspace_root=tmp_path, build_dir=tmp_path / "build", profile=OptimizationLevel.Debug)
    rendered = [c.argv[0] for c in s.run_cmds(ctx)]
    assert str(app.output_path(ctx)) in rendered[0]
    assert "user@host:/tmp/" in rendered[0]
    assert rendered[1].endswith("/tmp/MyApp")


def test_script_unknown_template_attribute_errors(tmp_project, tmp_path):
    (tmp_path / "main.c").write_text("int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="MyApp", srcs=[tmp_path / "main.c"])
        s = Script(name="bad", deps={"app": app}, cmds=["echo {app.bogus}"])

    ctx = BuildContext(workspace_root=tmp_path, build_dir=tmp_path / "build", profile=OptimizationLevel.Debug)
    with pytest.raises(AttributeError, match="no template attribute 'bogus'"):
        s.run_cmds(ctx)
