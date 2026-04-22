from pathlib import Path

from devops.context import Tool


def test_string_spec_becomes_single_element_argv():
    assert Tool.of("clang").argv == ("clang",)


def test_list_spec_kept_verbatim():
    t = Tool.of(["docker", "run", "custom-cc"])
    assert t.argv == ("docker", "run", "custom-cc")


def test_invoke_prepends_argv():
    t = Tool.of(["docker", "run", "custom-cc"])
    assert t.invoke(["-c", "main.c"]) == ("docker", "run", "custom-cc", "-c", "main.c")


def test_placeholders_substitute_workspace_project_cwd():
    t = Tool.of([
        "docker", "run", "--rm",
        "-v", "{workspace}:{workspace}",
        "-w", "{cwd}",
        "cc:v1",
    ])
    r = t.resolved_for(
        workspace=Path("/ws"),
        project=Path("/ws/proj"),
        cwd=Path("/ws/proj/sub"),
    )
    assert r.argv == (
        "docker", "run", "--rm",
        "-v", "/ws:/ws",
        "-w", "/ws/proj/sub",
        "cc:v1",
    )


def test_placeholders_fall_back_to_project_when_cwd_missing():
    t = Tool.of(["cc", "-w", "{cwd}"])
    r = t.resolved_for(workspace=Path("/ws"), project=Path("/ws/p"), cwd=None)
    assert r.argv == ("cc", "-w", "/ws/p")
