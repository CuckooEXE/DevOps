from devops.version import resolve_version


def test_explicit_override_wins(tmp_path):
    (tmp_path / "VERSION").write_text("0.1.0\n")
    assert resolve_version(tmp_path, "2.0.0") == "2.0.0"


def test_version_file_fallback(tmp_path):
    (tmp_path / "VERSION").write_text("0.1.0\n")
    # no git in tmp_path
    assert resolve_version(tmp_path, None) == "0.1.0"


def test_unknown_when_nothing_available(tmp_path):
    assert resolve_version(tmp_path, None) == "0.0.0-unknown"
