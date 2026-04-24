"""devops watch — rebuild-on-change inner loop.

Runs a build once, then watches every input the build actually touched
(including headers discovered by each Command's depfile) and rebuilds
whenever any of them changes. Forward-reachability across ``Target.deps``
means editing ``libmath/src/add.c`` invalidates ``libmath`` and
everything that consumes it.

Design notes:

- The watcher is *roughly* right and the stamp cache takes the hit on
  false positives. A 250ms debounce coalesces editor-save bursts. We
  don't try to be surgical about which file maps to which Target — the
  cache in :mod:`devops.cache` is the source of truth for "needs work?"
- ``build.py`` changes trigger a full in-process re-discovery. The
  registry is reset and every ``build.py`` is re-imported. Old Target
  objects go out of scope.
- Watchdog is an optional dep. Install via ``pip install
  devops-builder[watch]``; without it, a polling fallback kicks in.
- Events for anything under ``ctx.build_dir`` are dropped (we'd otherwise
  rebuild in response to our own outputs).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Protocol

from devops import graph, registry
from devops.cache import parse_depfile
from devops.core.command import Command
from devops.core.target import Artifact, Target

if TYPE_CHECKING:
    from devops.context import BuildContext


def build_reverse_deps(targets: Iterable[Target]) -> dict[int, set[Target]]:
    """Child → consumers map, keyed by ``id(target)`` because Target isn't hashable."""
    rev: dict[int, set[Target]] = defaultdict(set)
    for t in targets:
        for dep in t.deps.values():
            rev[id(dep)].add(t)
    return rev


def expand_consumers(
    seeds: Iterable[Target],
    reverse_deps: dict[int, set[Target]],
) -> set[int]:
    """Forward-closure through ``reverse_deps``. Returns id()s."""
    seen: set[int] = set()
    stack: list[Target] = []
    for s in seeds:
        if id(s) not in seen:
            seen.add(id(s))
            stack.append(s)
    while stack:
        t = stack.pop()
        for consumer in reverse_deps.get(id(t), ()):
            if id(consumer) not in seen:
                seen.add(id(consumer))
                stack.append(consumer)
    return seen


def build_reverse_index(
    targets: Iterable[Target],
    ctx: "BuildContext",
    *,
    exclude_under: Path | None = None,
) -> dict[Path, set[int]]:
    """Map each input path (+ depfile-discovered header) → Artifact id()s.

    A failed ``build_cmds`` probe on one Target is swallowed so it
    doesn't hide coverage for the rest; the watcher is best-effort and
    the cache catches anything we missed.
    """
    idx: dict[Path, set[int]] = defaultdict(set)

    def _add(p: Path, t_id: int) -> None:
        if exclude_under is not None:
            try:
                p.resolve().relative_to(exclude_under)
                return  # under build dir, skip
            except ValueError:
                pass
        idx[p].add(t_id)

    for t in targets:
        if not isinstance(t, Artifact):
            continue
        try:
            cmds = t.build_cmds(ctx)
        except Exception:
            continue
        for c in cmds:
            for p in c.inputs:
                _add(p, id(t))
            if c.depfile is not None and c.depfile.is_file():
                for hdr in parse_depfile(c.depfile):
                    _add(hdr, id(t))
        for p in t.extra_inputs:
            _add(p, id(t))
    return idx


def affected_targets(
    changed: Iterable[Path],
    reverse_index: dict[Path, set[int]],
    reverse_deps: dict[int, set[Target]],
    targets_by_id: dict[int, Target],
) -> list[Target]:
    """Union of direct hits + forward-reachable consumers, de-duplicated."""
    seeds: list[Target] = []
    for p in changed:
        for tid in reverse_index.get(p, ()):
            t = targets_by_id.get(tid)
            if t is not None:
                seeds.append(t)
    if not seeds:
        return []
    affected_ids = expand_consumers(seeds, reverse_deps)
    return [targets_by_id[tid] for tid in affected_ids if tid in targets_by_id]


def collect_build_py_paths(workspace_root: Path) -> set[Path]:
    """Every build.py under the workspace, plus the devops.toml itself."""
    from devops.workspace import _iter_build_files

    paths = {p.resolve() for p in _iter_build_files(workspace_root)}
    toml = (workspace_root / "devops.toml").resolve()
    if toml.is_file():
        paths.add(toml)
    return paths


# ---------- watcher backends ----------


class Watcher(Protocol):
    def start(self, root: Path, on_change: Callable[[Path], None]) -> None: ...
    def stop(self) -> None: ...


class _PollingWatcher:
    """mtime poll loop — fallback when watchdog isn't installed."""

    def __init__(self, interval: float = 1.0) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, root: Path, on_change: Callable[[Path], None]) -> None:
        mtimes: dict[Path, float] = {}

        def loop() -> None:
            # Seed the mtime table
            for p in root.rglob("*"):
                try:
                    mtimes[p] = p.stat().st_mtime
                except OSError:
                    continue
            while not self._stop.is_set():
                for p in root.rglob("*"):
                    try:
                        m = p.stat().st_mtime
                    except OSError:
                        continue
                    prev = mtimes.get(p)
                    if prev is None or m != prev:
                        mtimes[p] = m
                        if prev is not None:  # skip first-time discoveries
                            on_change(p)
                self._stop.wait(self._interval)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


class _WatchdogWatcher:
    """watchdog.Observer-backed watcher."""

    def __init__(self) -> None:
        # watchdog is an optional dep; Any sidesteps needing a stub.
        self._observer: Any = None

    def start(self, root: Path, on_change: Callable[[Path], None]) -> None:
        from watchdog.events import FileSystemEventHandler  # type: ignore[import-not-found]
        from watchdog.observers import Observer  # type: ignore[import-not-found]

        class Handler(FileSystemEventHandler):  # type: ignore[misc]
            def on_any_event(self, event: Any) -> None:
                if event.is_directory:
                    return
                on_change(Path(event.src_path))

        self._observer = Observer()
        self._observer.schedule(Handler(), str(root), recursive=True)
        self._observer.start()

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)


def _make_watcher(force_polling: bool) -> Watcher:
    if force_polling:
        return _PollingWatcher()
    try:
        import watchdog  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return _PollingWatcher()
    return _WatchdogWatcher()


# ---------- event loop ----------


class _Debouncer:
    """Coalesce a burst of events into a single callback."""

    def __init__(self, delay_ms: int, fire: Callable[[set[Path]], None]) -> None:
        self._delay = delay_ms / 1000.0
        self._fire = fire
        self._pending: set[Path] = set()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def add(self, path: Path) -> None:
        with self._lock:
            self._pending.add(path)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._trigger)
            self._timer.daemon = True
            self._timer.start()

    def _trigger(self) -> None:
        with self._lock:
            batch = self._pending
            self._pending = set()
            self._timer = None
        if batch:
            self._fire(batch)

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending.clear()


def _build_once(
    roots: list[Target],
    ctx: "BuildContext",
    run_commands: Callable[[list[Command], "BuildContext"], None],
) -> bool:
    """Build ``roots`` and their transitive deps. Returns True on success."""
    try:
        ordered = graph.topo_order(roots)
    except ValueError as e:
        print(f"devops watch: {e}")
        return False
    ok = True
    for dep in ordered:
        if not isinstance(dep, Artifact):
            continue
        try:
            run_commands(dep.build_cmds(ctx), ctx)
        except Exception as e:  # keep the watcher alive across failures
            print(f"devops watch: {dep.qualified_name} failed: {e}")
            ok = False
            break
    return ok


def run(
    names: list[str] | None,
    ctx: "BuildContext",
    run_commands: Callable[[list[Command], "BuildContext"], None],
    *,
    debounce_ms: int = 250,
    clear_screen: bool = False,
    poll: bool = False,
) -> int:
    """Block forever, rebuilding ``names`` (or all Artifacts) on change.

    ``run_commands(cmds, ctx)`` is injected so callers control how
    commands actually run (typically ``devops.cli._run_commands``).
    """
    from devops.workspace import discover_projects

    workspace_root = ctx.workspace_root
    build_dir = ctx.build_dir.resolve()

    def _select_roots() -> list[Target]:
        all_targets = registry.all_targets()
        if names:
            return [registry.resolve(n) for n in names]
        return [t for t in all_targets if isinstance(t, Artifact)]

    roots = _select_roots()
    reverse_deps = build_reverse_deps(registry.all_targets())
    targets_by_id = {id(t): t for t in registry.all_targets()}
    build_py_paths = collect_build_py_paths(workspace_root)

    print(f"devops watch: initial build ({len(roots)} root(s))")
    _build_once(roots, ctx, run_commands)

    # Post-build re-index: depfiles are now populated, so header coverage
    # is complete from this point on.
    reverse_index = build_reverse_index(
        registry.all_targets(), ctx, exclude_under=build_dir
    )
    print(f"devops watch: watching {len(reverse_index)} path(s)")

    rebuild_lock = threading.Lock()

    def _on_batch(paths: set[Path]) -> None:
        nonlocal roots, reverse_deps, targets_by_id, build_py_paths, reverse_index

        # Hold the lock across every state read + write. Two Timer
        # threads can fire back-to-back if a new event arrives while
        # _trigger is already running, so serialize to keep the nonlocal
        # state consistent between branches.
        with rebuild_lock:
            # build.py / devops.toml change → full reload
            if any(p.resolve() in build_py_paths for p in paths):
                print("devops watch: build.py changed — re-discovering")
                try:
                    discover_projects(workspace_root)
                except Exception as e:
                    print(f"devops watch: discovery failed: {e}")
                    return
                roots = _select_roots()
                reverse_deps = build_reverse_deps(registry.all_targets())
                targets_by_id = {id(t): t for t in registry.all_targets()}
                build_py_paths = collect_build_py_paths(workspace_root)
                _build_once(roots, ctx, run_commands)
                reverse_index = build_reverse_index(
                    registry.all_targets(), ctx, exclude_under=build_dir
                )
                return

            affected = affected_targets(
                paths, reverse_index, reverse_deps, targets_by_id
            )
            # Only rebuild Targets the user asked to watch (or their deps/consumers).
            roots_set = {id(r) for r in roots}
            reachable_from_roots = expand_consumers(roots, reverse_deps) | roots_set
            affected_in_scope = [t for t in affected if id(t) in reachable_from_roots]
            if not affected_in_scope:
                return
            if clear_screen:
                print("\033[2J\033[H", end="")
            names_list = ", ".join(t.name for t in affected_in_scope[:5])
            extra = f" (+{len(affected_in_scope) - 5} more)" if len(affected_in_scope) > 5 else ""
            print(f"devops watch: rebuilding {names_list}{extra}")
            _build_once(affected_in_scope, ctx, run_commands)
            # Refresh index in case new depfiles appeared
            reverse_index = build_reverse_index(
                registry.all_targets(), ctx, exclude_under=build_dir
            )

    debouncer = _Debouncer(debounce_ms, _on_batch)
    watcher = _make_watcher(force_polling=poll)

    def _on_change(path: Path) -> None:
        # Drop events under build_dir — they're our own outputs.
        try:
            path.resolve().relative_to(build_dir)
            return
        except ValueError:
            pass
        debouncer.add(path)

    watcher.start(workspace_root, _on_change)
    print("devops watch: ready (Ctrl-C to exit)")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\ndevops watch: shutting down")
    finally:
        debouncer.cancel()
        watcher.stop()
    return 0
