"""devops — CLI entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from devops import graph, registry
from devops.context import BuildContext, load_toolchain
from devops.core import runner
from devops.core.command import Command
from devops.core.target import Artifact, Script, Target
from devops.options import OptimizationLevel
from devops.targets.tests import TestTarget
from devops.workspace import discover_projects, find_workspace_root


app = typer.Typer(add_completion=False, no_args_is_help=True, help="Multi-language Python-defined build system.")


# ---------- plumbing ----------


def _prepare(profile: OptimizationLevel = OptimizationLevel.Debug, verbose: bool = False, dry_run: bool = False) -> BuildContext:
    root = find_workspace_root(Path.cwd())
    discover_projects(root)
    return BuildContext(
        workspace_root=root,
        build_dir=root / "build",
        profile=profile,
        verbose=verbose,
        dry_run=dry_run,
        toolchain=load_toolchain(root),
    )


def _resolve(name: str, *, current=None) -> Target:
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
def describe(names: list[str] = typer.Argument(None)):
    """Pretty-print targets and their deps."""
    _prepare()
    targets = registry.all_targets()
    if names:
        targets = [_resolve(n) for n in names]
    for t in targets:
        typer.echo(t.describe())
        if t.deps:
            typer.echo(f"  deps: {', '.join(f'{k}={v.qualified_name}' for k, v in t.deps.items())}")
        typer.echo("")


@app.command()
def build(
    name: str,
    profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Build an artifact (and its transitive deps)."""
    ctx = _prepare(profile=profile, verbose=verbose)
    t = _resolve(name)
    if not isinstance(t, Artifact):
        typer.echo(f"error: {name} is a {type(t).__name__}, not an Artifact", err=True)
        raise typer.Exit(1)
    _build_transitively(t, ctx)
    typer.echo(f"built: {t.output_path(ctx)}")


@app.command()
def run(name: str, profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile"), verbose: bool = False, dry_run: bool = typer.Option(False, "--dry-run")):
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
def lint(names: list[str] = typer.Argument(None), profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile"), verbose: bool = False):
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
def test(names: list[str] = typer.Argument(None), profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile"), verbose: bool = False):
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
def version(name: str = typer.Argument(None)):
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
def cmds(name: str, profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile")):
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


@app.command()
def clean(names: list[str] = typer.Argument(None), profile: OptimizationLevel = typer.Option(OptimizationLevel.Debug, "--profile")):
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
