import pytest

from devops.targets.c_cpp import glob_sources


def _touch(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")


def test_glob_expands_matching_files(tmp_path):
    for name in ("a.c", "b.c", "inner/c.c"):
        _touch(tmp_path / name)
    result = glob_sources(tmp_path, "**/*.c")
    assert [p.name for p in result] == ["a.c", "b.c", "c.c"]


def test_glob_exclude_filters(tmp_path):
    for name in ("a.c", "a_test.c", "b.c"):
        _touch(tmp_path / name)
    result = glob_sources(tmp_path, "*.c", exclude=["*_test.c"])
    assert sorted(p.name for p in result) == ["a.c", "b.c"]


def test_glob_empty_raises_unless_allowed(tmp_path):
    with pytest.raises(FileNotFoundError):
        glob_sources(tmp_path, "*.c")
    # allow_empty=True returns []
    assert glob_sources(tmp_path, "*.c", allow_empty=True) == []


def test_glob_multiple_patterns(tmp_path):
    for name in ("main.c", "src/a.c", "src/b.c"):
        _touch(tmp_path / name)
    result = glob_sources(tmp_path, ["main.c", "src/*.c"])
    assert sorted(p.name for p in result) == ["a.c", "b.c", "main.c"]
