from devops import registry
from devops.workspace import discover_projects, find_workspace_root


def test_find_workspace_root_locates_devops_toml(tmp_path):
    (tmp_path / "devops.toml").write_text("")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert find_workspace_root(sub) == tmp_path.resolve()


def test_discover_projects_imports_each_build_py(tmp_path):
    (tmp_path / "devops.toml").write_text("")
    proj_a = tmp_path / "a"
    proj_a.mkdir()
    (proj_a / "main.c").write_text("int main(){return 0;}")
    (proj_a / "build.py").write_text(
        "from builder import ElfBinary, glob\n"
        "ElfBinary(name='aapp', srcs=glob('main.c'))\n"
    )
    proj_b = tmp_path / "b"
    proj_b.mkdir()
    (proj_b / "build.py").write_text(
        "from builder import Script\n"
        "Script(name='bscript', cmds=['echo hi'])\n"
    )

    projects = discover_projects(tmp_path)
    names = sorted(p.name for p in projects)
    assert names == ["a", "b"]

    targets = registry.all_targets()
    tnames = sorted(t.qualified_name for t in targets)
    assert tnames == ["a::aapp", "b::bscript"]


def test_resolve_ambiguous_name_errors(tmp_path):
    (tmp_path / "devops.toml").write_text("")
    for p in ("a", "b"):
        d = tmp_path / p
        d.mkdir()
        (d / "build.py").write_text(
            "from builder import Script\n"
            "Script(name='shared', cmds=['echo hi'])\n"
        )

    discover_projects(tmp_path)
    import pytest

    with pytest.raises(LookupError, match="ambiguous"):
        registry.resolve("shared")

    # Qualified resolution works
    t = registry.resolve("a::shared")
    assert t.project.name == "a"
