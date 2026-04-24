"""devops — CLI entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from devops import bootstrap as bootstrap_mod
from devops import graph, registry
from devops.context import BuildContext, load_toolchains
from devops.core import runner
from devops.core.command import Command
from devops.core.target import Artifact, Project, Script, Target
from devops.options import OptimizationLevel
from devops.targets.install import Install
from devops.targets.tests import TestTarget
from devops.workspace import discover_projects, find_workspace_root


app = typer.Typer(add_completion=True, no_args_is_help=True, help="Multi-language Python-defined build system.")


# ---------- completion helpers ----------


def _complete_any_target(incomplete: str) -> list[str]:
    """Return target names (short + qualified) matching `incomplete`.

    Errors are swallowed because completion must never crash the shell.
    """
    try:
        root = find_workspace_root(Path.cwd())
        discover_projects(root)
    except Exception:
        return []
    names: set[str] = set()
    for t in registry.all_targets():
        names.add(t.name)
        names.add(t.qualified_name)
    return sorted(n for n in names if n.startswith(incomplete))


def _complete_artifact(incomplete: str) -> list[str]:
    try:
        root = find_workspace_root(Path.cwd())
        discover_projects(root)
    except Exception:
        return []
    names: set[str] = set()
    for t in registry.all_targets():
        if isinstance(t, Artifact):
            names.add(t.name)
            names.add(t.qualified_name)
    return sorted(n for n in names if n.startswith(incomplete))


def _complete_runnable(incomplete: str) -> list[str]:
    """Scripts + executable Artifacts (ElfBinary / GoogleTest / Python apps)."""
    from devops.targets.c_cpp import ElfBinary, ElfSharedObject
    from devops.targets.python import PythonApp, PythonShiv
    from devops.targets.tests import GoogleTest
    from devops.targets.zig import ZigBinary

    try:
        root = find_workspace_root(Path.cwd())
        discover_projects(root)
    except Exception:
        return []
    names: set[str] = set()
    for t in registry.all_targets():
        if isinstance(t, Script):
            names.add(t.name)
            names.add(t.qualified_name)
        elif isinstance(t, ElfBinary) and not isinstance(t, ElfSharedObject):
            names.add(t.name)
            names.add(t.qualified_name)
        elif isinstance(t, (GoogleTest, ZigBinary, PythonApp, PythonShiv)):
            names.add(t.name)
            names.add(t.qualified_name)
    return sorted(n for n in names if n.startswith(incomplete))


def _complete_testable(incomplete: str) -> list[str]:
    try:
        root = find_workspace_root(Path.cwd())
        discover_projects(root)
    except Exception:
        return []
    names: set[str] = set()
    for t in registry.all_targets():
        if isinstance(t, TestTarget):
            names.add(t.name)
            names.add(t.qualified_name)
    return sorted(n for n in names if n.startswith(incomplete))


def _complete_installable(incomplete: str) -> list[str]:
    try:
        root = find_workspace_root(Path.cwd())
        discover_projects(root)
    except Exception:
        return []
    names: set[str] = set()
    for t in registry.all_targets():
        if isinstance(t, Install):
            names.add(t.name)
            names.add(t.qualified_name)
    return sorted(n for n in names if n.startswith(incomplete))


# ---------- plumbing ----------


def _prepare(profile: OptimizationLevel = OptimizationLevel.Debug, verbose: bool = False, dry_run: bool = False) -> BuildContext:
    root = find_workspace_root(Path.cwd())
    discover_projects(root)
    toolchains = load_toolchains(root)
    return BuildContext(
        workspace_root=root,
        build_dir=root / "build",
        profile=profile,
        verbose=verbose,
        dry_run=dry_run,
        toolchain=toolchains["host"],
        toolchains=toolchains,
    )


def _resolve(name: str, *, current: Project | None = None) -> Target:
    try:
        return registry.resolve(name, current=current)
    except LookupError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)


def _run_commands(cmds: list[Command], ctx: BuildContext) -> None:
    try:
        runner.run_all(cmds, verbose=ctx.verbose, dry_run=ctx.dry_run)
    except runner.ToolMissing as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2)
    except runner.CommandFailed as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(e.returncode)


def _build_transitively(t: Artifact, ctx: BuildContext) -> None:
    for dep in graph.topo_order([t]):
        if isinstance(dep, Artifact):
            _run_commands(dep.build_cmds(ctx), ctx)


# ---------- subcommands ----------


@app.command()
def describe(names: list[str] = typer.Argument(None, autocompletion=_complete_any_target)) -> None:
    """Pretty-print targets and their deps."""
    _prepare()
    targets = registry.all_targets()
    if names:
        targets = [_resolve(n) for n in names]
    for t in targets:
        typer.echo(t.describe())
        if t.deps:
            typer.echo(f"  deps: {', '.join(f'{k}={v.qualified_name}' for k, v in t.deps.items())}")
        if t.doc:
            for line in t.doc.splitlines():
                typer.echo(f"  | {line}")
        typer.echo("")


@app.command()
def build(
    name: str = typer.Argument(..., autocompletion=_complete_artifact),
    profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Build an artifact (and its transitive deps)."""
    ctx = _prepare(profile=profile, verbose=verbose)
    t = _resolve(name)
    if not isinstance(t, Artifact):
        typer.echo(f"error: {name} is a {type(t).__name__}, not an Artifact", err=True)
        raise typer.Exit(1)
    _build_transitively(t, ctx)
    typer.echo(f"built: {t.output_path(ctx)}")


@app.command()
def run(
    name: str = typer.Argument(..., autocompletion=_complete_runnable),
    profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile"),
    verbose: bool = False,
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Execute an Artifact's output, or run a Script."""
    ctx = _prepare(profile=profile, verbose=verbose, dry_run=dry_run)
    t = _resolve(name)
    if isinstance(t, Script):
        # Build any Artifact deps first
        for dep in graph.topo_order([t]):
            if isinstance(dep, Artifact):
                _run_commands(dep.build_cmds(ctx), ctx)
        _run_commands(t.run_cmds(ctx), ctx)
        return
    if isinstance(t, Artifact):
        # Special case: libraries aren't runnable
        from devops.targets.c_cpp import ElfSharedObject, HeadersOnly, StaticLibrary

        if isinstance(t, (ElfSharedObject, StaticLibrary, HeadersOnly)):
            typer.echo(f"error: {name} is a library; libraries can't be run", err=True)
            raise typer.Exit(1)
        _build_transitively(t, ctx)
        _run_commands([Command(argv=(str(t.output_path(ctx)),), cwd=t.project.root, label=f"exec {t.name}")], ctx)
        return
    typer.echo(f"error: don't know how to run {type(t).__name__}", err=True)
    raise typer.Exit(1)


@app.command()
def lint(
    names: list[str] = typer.Argument(None, autocompletion=_complete_artifact),
    profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile"),
    verbose: bool = False,
) -> None:
    """Run lint commands for selected (or all) targets."""
    ctx = _prepare(profile=profile, verbose=verbose)
    targets: list[Target] = [_resolve(n) for n in names] if names else registry.all_targets()
    failures: list[str] = []
    for t in targets:
        if not isinstance(t, Artifact):
            continue
        cmds = t.lint_cmds(ctx)
        if not cmds:
            continue
        try:
            runner.run_all(cmds, verbose=ctx.verbose, dry_run=ctx.dry_run, use_cache=False)
        except runner.CommandFailed as e:
            failures.append(f"{t.qualified_name}: {e}")
        except runner.ToolMissing as e:
            failures.append(f"{t.qualified_name}: {e}")
    if failures:
        typer.echo("\nlint failures:", err=True)
        for f in failures:
            typer.echo(f"  {f}", err=True)
        raise typer.Exit(1)
    typer.echo("lint ok")


@app.command()
def test(
    names: list[str] = typer.Argument(None, autocompletion=_complete_testable),
    profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile"),
    verbose: bool = False,
) -> None:
    """Build and run all (or selected) test targets."""
    ctx = _prepare(profile=profile, verbose=verbose)
    if names:
        targets = [_resolve(n) for n in names]
    else:
        targets = [t for t in registry.all_targets() if isinstance(t, TestTarget)]
    failures: list[str] = []
    for t in targets:
        if not isinstance(t, TestTarget):
            typer.echo(f"skipping {t.qualified_name}: not a TestTarget", err=True)
            continue
        _build_transitively(t, ctx)
        try:
            runner.run_all(t.test_cmds(ctx), verbose=ctx.verbose, dry_run=ctx.dry_run, use_cache=False)
            typer.echo(f"PASS {t.qualified_name}")
        except (runner.CommandFailed, runner.ToolMissing) as e:
            failures.append(f"{t.qualified_name}: {e}")
            typer.echo(f"FAIL {t.qualified_name}: {e}", err=True)
    if failures:
        raise typer.Exit(1)


@app.command()
def version(name: str = typer.Argument(None, autocompletion=_complete_artifact)) -> None:
    """Print an artifact's version."""
    if name is None:
        typer.echo("error: artifact name required", err=True)
        raise typer.Exit(1)
    _prepare()
    t = _resolve(name)
    if not isinstance(t, Artifact):
        typer.echo(f"error: {name} is not an Artifact", err=True)
        raise typer.Exit(1)
    typer.echo(t.version())


@app.command()
def cmds(
    name: str = typer.Argument(..., autocompletion=_complete_artifact),
    profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile"),
) -> None:
    """Print the commands that would run for a build (without running them)."""
    ctx = _prepare(profile=profile)
    t = _resolve(name)
    if not isinstance(t, Artifact):
        typer.echo(f"error: {name} is not an Artifact", err=True)
        raise typer.Exit(1)
    for dep in graph.topo_order([t]):
        if isinstance(dep, Artifact):
            for c in dep.build_cmds(ctx):
                typer.echo(c.rendered())


@app.command(name="install")
def install_cmd(
    names: list[str] = typer.Argument(None, autocompletion=_complete_installable),
    profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run Install targets: stage binaries/libs under a dest, or pip install wheels.

    With no names, runs every Install target in the workspace.
    """
    ctx = _prepare(profile=profile, verbose=verbose)
    if names:
        targets = [_resolve(n) for n in names]
    else:
        targets = [t for t in registry.all_targets() if isinstance(t, Install)]
    if not targets:
        typer.echo("no Install targets declared", err=True)
        raise typer.Exit(1)
    for t in targets:
        if not isinstance(t, Install):
            typer.echo(f"error: {t.qualified_name} is a {type(t).__name__}, not an Install", err=True)
            raise typer.Exit(1)
        # Build the artifact (+ its transitive deps) first
        _build_transitively(t.artifact, ctx)
        _run_commands(t.install_cmds(ctx), ctx)
        typer.echo(f"installed: {t.qualified_name}")


@app.command()
def bootstrap(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Install tools declared in devops.toml's [bootstrap] section.

    Runs apt → pip → run in order. Idempotent in the common case (apt-get
    install skips already-installed packages, pip re-resolves cheaply).

    Run this on a fresh VM / in CI / inside a Dockerfile:

        devops bootstrap && devops doctor && devops build <name>
    """
    root = find_workspace_root(Path.cwd())
    cfg = bootstrap_mod.load_bootstrap(root)

    if cfg.is_empty:
        typer.echo("no [bootstrap] section in devops.toml (or it's empty) — nothing to do")
        return

    cmds = bootstrap_mod.bootstrap_commands(cfg, cwd=root)
    if verbose:
        typer.echo(f"[bootstrap] {len(cmds)} step(s) from {cfg._source}")
        if cfg.apt:
            typer.echo(f"  apt ({len(cfg.apt)}): {' '.join(cfg.apt)}")
        if cfg.pip:
            typer.echo(f"  pip ({len(cfg.pip)}): {' '.join(cfg.pip)}  [args: {' '.join(cfg.pip_args)}]")
        if cfg.run:
            typer.echo(f"  run ({len(cfg.run)}): {cfg.run[0]!r}…")

    try:
        runner.run_all(cmds, verbose=verbose, dry_run=dry_run, use_cache=False)
    except runner.ToolMissing as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(2)
    except runner.CommandFailed as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(e.returncode)
    typer.echo("bootstrap ok")


@app.command()
def doctor(
    profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Pre-flight check: every tool any target needs is on PATH.

    Walks every registered target, unions declared ``required_tools=`` with
    the ``argv[0]`` of every non-shell Command it produces, resolves each
    through ``shutil.which``. Exits non-zero if any tool is missing.

    Run this early in CI (before `devops build`) so a missing tool fails
    fast with a consolidated list rather than surfacing mid-build.
    """
    import shutil

    ctx = _prepare(profile=profile, verbose=verbose)

    # Skip argv[0]s that point at things this workspace *produces* —
    # e.g. a GoogleTest's argv[0] is the test binary itself, which isn't
    # a "tool to install" but an earlier target's output.
    build_dir_str = str(ctx.build_dir)
    needed: dict[str, list[str]] = {}  # tool -> list of targets that need it
    for t in registry.all_targets():
        for tool in t.collect_tool_names(ctx):
            if tool.startswith(build_dir_str):
                continue  # build-produced artifact, not a tool
            needed.setdefault(tool, []).append(t.qualified_name)

    missing: list[str] = []
    for tool in sorted(needed):
        if shutil.which(tool) is None and not Path(tool).is_file():
            missing.append(tool)

    if verbose:
        typer.echo(f"checked {len(needed)} distinct tool(s) across {len(registry.all_targets())} target(s)")
        for tool in sorted(needed):
            status = "MISSING" if tool in missing else "ok"
            consumers = ", ".join(sorted(set(needed[tool])))
            typer.echo(f"  [{status:>7}] {tool:<30} — {consumers}")

    if missing:
        typer.echo("", err=True)
        typer.echo(f"error: {len(missing)} tool(s) missing from PATH:", err=True)
        for tool in missing:
            consumers = ", ".join(sorted(set(needed[tool])))
            typer.echo(f"  {tool}  ({consumers})", err=True)
        typer.echo("", err=True)
        # If [bootstrap] is defined, nudge the user toward it.
        cfg = bootstrap_mod.load_bootstrap(ctx.workspace_root)
        if not cfg.is_empty:
            typer.echo("Try: devops bootstrap", err=True)
        else:
            typer.echo(
                "Install them (apt / pip / vendor download), add a [bootstrap] "
                "section to devops.toml, or wrap them via [toolchain].",
                err=True,
            )
        raise typer.Exit(1)

    typer.echo(f"doctor ok — {len(needed)} tool(s) present")


@app.command(name="watch")
def watch_cmd(
    names: list[str] = typer.Argument(None, autocompletion=_complete_artifact),
    profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    debounce_ms: int = typer.Option(250, "--debounce-ms"),
    clear_screen: bool = typer.Option(False, "--clear-screen", "-c"),
    poll: bool = typer.Option(False, "--poll", help="Force polling (skip watchdog)."),
) -> None:
    """Build once, then rebuild affected targets on file change.

    With no names, watches every Artifact in the workspace. With names,
    limits rebuilds to those targets and their forward-reachable
    consumers.
    """
    from devops import watch as watch_mod

    ctx = _prepare(profile=profile, verbose=verbose)
    raise typer.Exit(watch_mod.run(
        names=names,
        ctx=ctx,
        run_commands=_run_commands,
        debounce_ms=debounce_ms,
        clear_screen=clear_screen,
        poll=poll,
    ))


@app.command(name="graph")
def graph_cmd(
    names: list[str] = typer.Argument(None, autocompletion=_complete_any_target),
    fmt: str = typer.Option("dot", "--format", "-f", help="dot, json, or text"),
    output: str = typer.Option("-", "--output", "-o", help="Write to file; '-' for stdout"),
    resolve_remotes: bool = typer.Option(
        False, "--resolve-remotes",
        help="Fetch and resolve GitRef/TarballRef/DirectoryRef dependencies (network).",
    ),
    profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile"),
) -> None:
    """Export the dependency DAG as dot, json, or text.

    With no names, exports every registered target. With names, exports
    the forward-transitive subgraph rooted at those targets. Remote refs
    are opaque nodes unless --resolve-remotes is set.
    """
    from devops import graph_export

    ctx = _prepare(profile=profile)
    roots: list[Target] | None
    if names:
        roots = [_resolve(n) for n in names]
    else:
        roots = None

    if fmt not in ("dot", "json", "text"):
        typer.echo(f"error: unknown --format {fmt!r} (want dot, json, text)", err=True)
        raise typer.Exit(1)

    rendered = graph_export.render(fmt, roots, ctx=ctx, resolve_remotes=resolve_remotes)

    if output == "-":
        typer.echo(rendered, nl=False)
    else:
        Path(output).write_text(rendered)


@app.command()
def clean(
    names: list[str] = typer.Argument(None, autocompletion=_complete_artifact),
    profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile"),
) -> None:
    """Remove build outputs for selected (or all) artifacts."""
    ctx = _prepare(profile=profile)
    targets: list[Target] = [_resolve(n) for n in names] if names else registry.all_targets()
    cmds: list[Command] = []
    for t in targets:
        if isinstance(t, Artifact):
            cmds.extend(t.clean_cmds(ctx))
    _run_commands(cmds, ctx)


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(app())
