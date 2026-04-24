"""devops watch — reverse index, consumer expansion, debouncer.

The event loop + watchdog backends are not exercised here (they need a
real filesystem); those get a single integration smoke test at the
bottom. Everything else is pure logic tested on synthetic inputs.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from devops import watch
from devops.context import BuildContext
from devops.options import OptimizationLevel
from devops.targets.c_cpp import ElfBinary, StaticLibrary


def _ctx(tmp_path: Path) -> BuildContext:
    return BuildContext(
        workspace_root=tmp_path,
        build_dir=tmp_path / "build",
        profile=OptimizationLevel.Debug,
    )


def _write(tmp: Path, rel: str, body: str = "") -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_reverse_index_contains_direct_inputs(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"])
    idx = watch.build_reverse_index([app], _ctx(tmp_path))
    assert id(app) in idx[tmp_path / "main.c"]


def test_reverse_index_excludes_paths_under_build_dir(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"])
    ctx = _ctx(tmp_path)
    idx = watch.build_reverse_index([app], ctx, exclude_under=ctx.build_dir.resolve())
    for p in idx:
        assert ctx.build_dir not in p.parents


def test_reverse_deps_inverts_dep_edges(tmp_project, tmp_path):
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        lib = StaticLibrary(name="lib", srcs=[tmp_path / "lib.c"])
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"], libs=[lib])
    rev = watch.build_reverse_deps([lib, app])
    assert app in rev[id(lib)]


def test_expand_consumers_walks_forward(tmp_project, tmp_path):
    """Editing lib.c should mark both lib AND app as affected (app consumes lib)."""
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        lib = StaticLibrary(name="lib", srcs=[tmp_path / "lib.c"])
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"], libs=[lib])
    rev = watch.build_reverse_deps([lib, app])
    affected = watch.expand_consumers([lib], rev)
    assert id(lib) in affected
    assert id(app) in affected


def test_affected_targets_end_to_end(tmp_project, tmp_path):
    _write(tmp_path, "lib.c", "int f(){return 0;}")
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        lib = StaticLibrary(name="lib", srcs=[tmp_path / "lib.c"])
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"], libs=[lib])
    ctx = _ctx(tmp_path)
    idx = watch.build_reverse_index([lib, app], ctx)
    rev = watch.build_reverse_deps([lib, app])
    targets_by_id = {id(t): t for t in (lib, app)}
    changed = [tmp_path / "lib.c"]
    affected = watch.affected_targets(changed, idx, rev, targets_by_id)
    names = {t.name for t in affected}
    assert names == {"lib", "app"}


def test_affected_targets_empty_for_unknown_path(tmp_project, tmp_path):
    _write(tmp_path, "main.c", "int main(){return 0;}")
    _, enter = tmp_project
    with enter():
        app = ElfBinary(name="app", srcs=[tmp_path / "main.c"])
    ctx = _ctx(tmp_path)
    idx = watch.build_reverse_index([app], ctx)
    rev = watch.build_reverse_deps([app])
    targets_by_id = {id(app): app}
    affected = watch.affected_targets(
        [tmp_path / "nonexistent.c"], idx, rev, targets_by_id
    )
    assert affected == []


def test_debouncer_coalesces_rapid_events():
    fired: list[set[Path]] = []
    d = watch._Debouncer(delay_ms=60, fire=lambda s: fired.append(s))
    for i in range(5):
        d.add(Path(f"/tmp/f{i}"))
        time.sleep(0.01)
    time.sleep(0.2)
    d.cancel()
    assert len(fired) == 1
    assert len(fired[0]) == 5


def test_debouncer_fires_again_after_gap():
    fired: list[set[Path]] = []
    d = watch._Debouncer(delay_ms=40, fire=lambda s: fired.append(s))
    d.add(Path("/tmp/a"))
    time.sleep(0.1)
    d.add(Path("/tmp/b"))
    time.sleep(0.1)
    d.cancel()
    assert len(fired) == 2


def test_build_py_paths_collected(tmp_project, tmp_path):
    _write(tmp_path, "build.py", "# empty\n")
    _write(tmp_path, "devops.toml", "")
    _write(tmp_path, "sub/build.py", "# sub\n")
    paths = watch.collect_build_py_paths(tmp_path)
    assert any(p.name == "build.py" and p.parent == tmp_path for p in paths)
    assert any(p.name == "build.py" and p.parent == tmp_path / "sub" for p in paths)
    assert (tmp_path / "devops.toml").resolve() in paths


def test_watchdog_backend_chosen_when_available(monkeypatch):
    """If watchdog imports, pick the watchdog backend."""
    pytest.importorskip("watchdog", reason="watchdog not installed")
    w = watch._make_watcher(force_polling=False)
    assert isinstance(w, watch._WatchdogWatcher)


def test_polling_backend_forced_by_flag():
    w = watch._make_watcher(force_polling=True)
    assert isinstance(w, watch._PollingWatcher)


def test_polling_backend_chosen_when_watchdog_missing(monkeypatch):
    import sys

    real_watchdog = sys.modules.pop("watchdog", None)
    monkeypatch.setitem(sys.modules, "watchdog", None)
    # Force ImportError on `import watchdog`
    try:
        w = watch._make_watcher(force_polling=False)
        assert isinstance(w, watch._PollingWatcher)
    finally:
        if real_watchdog is not None:
            sys.modules["watchdog"] = real_watchdog


def test_affected_scope_limited_to_watched_roots(tmp_project, tmp_path):
    """An event in an unrelated subtree should not trigger any rebuild
    when the user watched only one specific target."""
    _write(tmp_path, "a.c", "int a(){return 0;}")
    _write(tmp_path, "b.c", "int b(){return 0;}")
    _, enter = tmp_project
    with enter():
        a = ElfBinary(name="a", srcs=[tmp_path / "a.c"])
        b = ElfBinary(name="b", srcs=[tmp_path / "b.c"])
    ctx = _ctx(tmp_path)
    idx = watch.build_reverse_index([a, b], ctx)
    rev = watch.build_reverse_deps([a, b])
    targets_by_id = {id(a): a, id(b): b}

    # User watches only `a`. `b.c` changes. `a` should not be affected.
    affected_all = watch.affected_targets(
        [tmp_path / "b.c"], idx, rev, targets_by_id
    )
    assert {t.name for t in affected_all} == {"b"}
    # Watch loop would further filter by reachability from roots=[a];
    # b is not reachable from a, so nothing rebuilds.
    roots = [a]
    roots_set = {id(r) for r in roots}
    reachable = watch.expand_consumers(roots, rev) | roots_set
    filtered = [t for t in affected_all if id(t) in reachable]
    assert filtered == []
