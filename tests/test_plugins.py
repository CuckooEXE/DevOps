"""Plugin discovery, loading, API version gate, and Toolchain.extras wiring."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from devops import api, plugins
from devops.context import Tool, Toolchain, load_toolchains
from devops.core.target import Artifact


# ---------- fakes ----------


class _FakeEntryPoint:
    """Shape-compatible with importlib.metadata.EntryPoint for our loader."""

    def __init__(self, name: str, loadable):
        self.name = name
        self._obj = loadable
        self.module = getattr(loadable, "__module__", "fake_plugin")

    def load(self):
        return self._obj


@pytest.fixture(autouse=True)
def _reset_plugins():
    plugins.reset_for_tests()
    yield
    plugins.reset_for_tests()


def _patch_entry_points(monkeypatch, eps: list[_FakeEntryPoint]) -> None:
    monkeypatch.setattr(plugins._metadata, "entry_points", lambda group=None: eps)


def _dummy_module(name: str, min_api_version: str | None = None) -> types.ModuleType:
    m = types.ModuleType(name)
    if min_api_version is not None:
        m.MIN_API_VERSION = min_api_version
    sys.modules[name] = m
    return m


# ---------- bare-class entry point ----------


def test_bare_class_entry_point_registers(monkeypatch):
    mod = _dummy_module("plug_bare")

    class FooBinary(Artifact):
        __module__ = "plug_bare"

        def build_cmds(self, ctx):
            return []

        def output_path(self, ctx):
            return Path("/tmp/foo")

        def describe(self):
            return "FooBinary"

    mod.FooBinary = FooBinary

    _patch_entry_points(monkeypatch, [_FakeEntryPoint("bare", FooBinary)])
    loaded = plugins.load_plugins()
    assert len(loaded) == 1
    assert FooBinary in loaded[0].classes


# ---------- register(api) entry point ----------


def test_register_callable_installs_classes_and_tool_defaults(monkeypatch):
    mod = _dummy_module("plug_reg")

    class Foo2(Artifact):
        __module__ = "plug_reg"

        def build_cmds(self, ctx):
            return []

        def output_path(self, ctx):
            return Path("/tmp/foo2")

        def describe(self):
            return "Foo2"

    def register(api_mod):
        api_mod.register_target(Foo2)
        api_mod.DEFAULT_TOOLCHAIN_EXTRAS["cargo"] = api_mod.Tool.of("cargo")

    register.__module__ = "plug_reg"
    mod.register = register

    _patch_entry_points(monkeypatch, [_FakeEntryPoint("reg", register)])
    loaded = plugins.load_plugins()
    assert len(loaded) == 1
    assert Foo2 in loaded[0].classes
    assert "cargo" in api.DEFAULT_TOOLCHAIN_EXTRAS


# ---------- version gate ----------


def test_min_api_version_too_high_skips_plugin(monkeypatch, capsys):
    mod = _dummy_module("plug_future", min_api_version="99")

    class Fut(Artifact):
        __module__ = "plug_future"

        def build_cmds(self, ctx):
            return []

        def output_path(self, ctx):
            return Path("/tmp")

        def describe(self):
            return "Fut"

    mod.Fut = Fut
    _patch_entry_points(monkeypatch, [_FakeEntryPoint("future", Fut)])
    loaded = plugins.load_plugins()
    assert loaded == []
    err = capsys.readouterr().err
    assert "api version" in err


# ---------- error handling ----------


def test_import_error_does_not_crash_loader(monkeypatch, capsys):
    bad_ep = _FakeEntryPoint("bad", object())
    bad_ep.load = lambda: (_ for _ in ()).throw(ImportError("boom"))
    _patch_entry_points(monkeypatch, [bad_ep])
    loaded = plugins.load_plugins()
    assert loaded == []
    err = capsys.readouterr().err
    assert "boom" in err
    assert "failed to import" in err


def test_strict_mode_raises_on_import_error(monkeypatch):
    bad_ep = _FakeEntryPoint("bad", object())
    bad_ep.load = lambda: (_ for _ in ()).throw(ImportError("boom"))
    _patch_entry_points(monkeypatch, [bad_ep])
    monkeypatch.setenv("DEVOPS_STRICT_PLUGINS", "1")
    with pytest.raises(RuntimeError, match="failed to import"):
        plugins.load_plugins()


def test_non_target_entry_point_skipped(monkeypatch, capsys):
    _dummy_module("plug_wrong")
    not_a_target = 42
    ep = _FakeEntryPoint("wrong", not_a_target)
    ep.module = "plug_wrong"
    _patch_entry_points(monkeypatch, [ep])
    loaded = plugins.load_plugins()
    assert loaded == []
    err = capsys.readouterr().err
    assert "must be a Target subclass" in err


def test_register_raising_is_caught(monkeypatch, capsys):
    mod = _dummy_module("plug_oops")

    def register(api_mod):
        raise RuntimeError("nope")

    register.__module__ = "plug_oops"
    mod.register = register
    _patch_entry_points(monkeypatch, [_FakeEntryPoint("oops", register)])
    loaded = plugins.load_plugins()
    assert loaded == []
    err = capsys.readouterr().err
    assert "register() raised" in err


# ---------- idempotency ----------


def test_load_is_cached(monkeypatch):
    _dummy_module("plug_cached")

    class X(Artifact):
        __module__ = "plug_cached"

        def build_cmds(self, ctx):
            return []

        def output_path(self, ctx):
            return Path("/tmp")

        def describe(self):
            return "X"

    _patch_entry_points(monkeypatch, [_FakeEntryPoint("cached", X)])
    first = plugins.load_plugins()
    second = plugins.load_plugins()
    assert first is second


# ---------- Toolchain.extras ----------


def test_default_toolchain_extras_merged_into_every_toolchain(monkeypatch, tmp_path):
    api.DEFAULT_TOOLCHAIN_EXTRAS["cargo"] = Tool.of("cargo")
    try:
        tcs = load_toolchains(tmp_path)
        assert "cargo" in tcs["host"].extras
        assert tcs["host"].extras["cargo"].argv == ("cargo",)
    finally:
        api.DEFAULT_TOOLCHAIN_EXTRAS.pop("cargo", None)


def test_devops_toml_extras_wins_over_plugin_default(tmp_path):
    (tmp_path / "devops.toml").write_text(
        '[toolchain.extras]\ncargo = ["docker", "run", "ghcr.io/acme/cargo", "cargo"]\n'
    )
    api.DEFAULT_TOOLCHAIN_EXTRAS["cargo"] = Tool.of("cargo")
    try:
        tcs = load_toolchains(tmp_path)
        argv = tcs["host"].extras["cargo"].argv
        assert argv[0] == "docker"
        assert "ghcr.io/acme/cargo" in argv
    finally:
        api.DEFAULT_TOOLCHAIN_EXTRAS.pop("cargo", None)


def test_toolchain_extras_rejects_non_table(tmp_path):
    with pytest.raises(TypeError, match="must be a table"):
        Toolchain.from_config({"extras": "not-a-table"})


# ---------- builder facade injection ----------


def test_plugin_class_injected_into_builder_namespace(monkeypatch):
    mod = _dummy_module("plug_inject")

    class Widget(Artifact):
        __module__ = "plug_inject"

        def build_cmds(self, ctx):
            return []

        def output_path(self, ctx):
            return Path("/tmp")

        def describe(self):
            return "Widget"

    mod.Widget = Widget
    _patch_entry_points(monkeypatch, [_FakeEntryPoint("inject", Widget)])

    # Re-import builder to trigger _inject_plugin_classes under our patched EPs
    if "builder" in sys.modules:
        del sys.modules["builder"]
    import builder  # noqa: F401
    assert hasattr(builder, "Widget")
    assert builder.Widget is Widget


def test_name_collision_with_builtin_skipped(monkeypatch, capsys):
    mod = _dummy_module("plug_collide")

    class ElfBinary(Artifact):  # same name as built-in
        __module__ = "plug_collide"

        def build_cmds(self, ctx):
            return []

        def output_path(self, ctx):
            return Path("/tmp")

        def describe(self):
            return "fake ElfBinary"

    mod.ElfBinary = ElfBinary
    _patch_entry_points(monkeypatch, [_FakeEntryPoint("collide", ElfBinary)])

    if "builder" in sys.modules:
        del sys.modules["builder"]
    import builder  # noqa: F811
    # Should keep the real built-in, not the plugin's fake
    from devops.targets.c_cpp import ElfBinary as RealElfBinary
    assert builder.ElfBinary is RealElfBinary
    err = capsys.readouterr().err
    assert "already bound" in err
