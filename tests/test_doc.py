"""Target.doc normalisation — inspect.cleandoc dedents multi-line docs."""

from __future__ import annotations

from devops.core.target import Script


def test_doc_empty_if_unset(tmp_project):
    _, enter = tmp_project
    with enter():
        s = Script(name="s", cmds=["true"])
    assert s.doc == ""


def test_doc_single_line_preserved(tmp_project):
    _, enter = tmp_project
    with enter():
        s = Script(name="s", cmds=["true"], doc="short description")
    assert s.doc == "short description"


def test_doc_multiline_dedented(tmp_project):
    """A triple-quoted doc with source-level indentation on every line
    after the first should come out with the indentation collapsed."""
    _, enter = tmp_project
    with enter():
        s = Script(
            name="s",
            cmds=["true"],
            doc="""First line
            second line
            third line""",
        )
    # All three lines, with second and third no longer indented
    lines = s.doc.splitlines()
    assert len(lines) == 3
    assert lines[0] == "First line"
    assert lines[1] == "second line"
    assert lines[2] == "third line"


def test_doc_trailing_whitespace_trimmed(tmp_project):
    """`inspect.cleandoc` strips leading whitespace on the first line and
    common indentation on the rest, and drops fully-blank leading/trailing
    lines. It does NOT trim trailing whitespace on the final content line."""
    _, enter = tmp_project
    with enter():
        s = Script(name="s", cmds=["true"], doc="\n\n   hello   \n\n")
    # leading spaces on the (only) line are removed
    assert s.doc.startswith("hello")
    # and the doc is no longer surrounded by blank lines
    assert "\n\n" not in s.doc
