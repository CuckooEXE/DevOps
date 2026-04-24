"""Run / build / describe a target from a remote-ref spec.

Lets you do::

    devops run git+ssh://host/repo[@ref]::Target -- arg1 arg2

from any cwd, without being inside a devops workspace. Clones (or
reads) the source, imports its ``build.py``, builds the target
transitively, and (for ``run``) execs the resulting Artifact from
your current working directory.

The ambient BuildContext is synthetic:

- ``workspace_root`` = the fetched/clone dir (so Command cwds resolve
  correctly for the remote's build)
- ``build_dir`` = ``~/.cache/devops/run/<url-hash>/build/``
- ``toolchain`` loaded from the remote's ``devops.toml`` if present,
  else the built-in defaults.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from devops.context import BuildContext, load_toolchains
from devops.options import OptimizationLevel
from devops.remote import DirectoryRef, GitRef, Ref, TarballRef, resolve_remote_ref

if TYPE_CHECKING:
    from devops.core.target import Target


RUN_CACHE_ROOT = Path.home() / ".cache" / "devops" / "run"


def parse_spec(s: str) -> Ref | None:
    """Map a CLI string to a Ref subclass. Returns None for plain target names.

    Recognized prefixes:
        git+<scheme>://...         → GitRef (ssh, https, file, etc.)
        http:// / https://         → TarballRef
        file://...                 → TarballRef (resolver picks dir vs tar)
        / or ./ or ../             → DirectoryRef
    """
    if "::" not in s:
        return None
    url_part, target = s.rsplit("::", 1)
    if not target:
        return None

    if url_part.startswith("git+"):
        # GitRef expects the post-`git+` URL with the optional @ref peeled off.
        from devops.remote import _split_git_ref

        git_url = url_part[len("git+"):]
        bare, ref = _split_git_ref(git_url)
        return GitRef(url=bare, target=target, ref=ref)

    if url_part.startswith(("http://", "https://", "file://")):
        return TarballRef(url=url_part, target=target)

    if url_part.startswith(("/", "./", "../")):
        return DirectoryRef(path=url_part, target=target)

    return None


def _cache_key(ref: Ref) -> str:
    return hashlib.sha1(ref.to_spec().encode()).hexdigest()[:16]


def adhoc_context(
    target: "Target",
    ref: Ref,
    *,
    profile: OptimizationLevel = OptimizationLevel.Debug,
    verbose: bool = False,
    dry_run: bool = False,
) -> BuildContext:
    """Build a BuildContext scoped to a remote target's project."""
    workspace = target.project.root
    build_dir = RUN_CACHE_ROOT / _cache_key(ref) / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    toolchains = load_toolchains(workspace)
    return BuildContext(
        workspace_root=workspace,
        build_dir=build_dir,
        profile=profile,
        verbose=verbose,
        dry_run=dry_run,
        toolchain=toolchains["host"],
        toolchains=toolchains,
    )


def resolve(spec: str) -> tuple[Ref, "Target"]:
    """Parse + resolve in one call. Raises ValueError on an unparseable spec."""
    ref = parse_spec(spec)
    if ref is None:
        raise ValueError(f"not a remote spec: {spec!r}")
    target = resolve_remote_ref(ref)
    return ref, target
