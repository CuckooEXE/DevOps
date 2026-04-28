"""Helpers for resolving Target / Ref specs at build time.

Most artifact kwargs accept some mix of:

    str / Path     — a literal filesystem path
    Target         — another build target (eager: caller holds the object)
    Ref            — a remote reference (GitRef / TarballRef / DirectoryRef);
                     resolves at build_cmds time via the network/cache layer

``resolve_target_spec`` centralizes the lazy resolution so each artifact
doesn't repeat the same isinstance ladder. ``inline_ref_build_cmds``
emits the build_cmds for every Ref-resolved upstream before the
consuming artifact's own commands — Refs aren't in ``deps`` (resolution
is lazy and network-backed), so topo-sort can't see them and the
consuming artifact has to schedule them itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from devops.core.target import Target
from devops.remote import Ref, resolve_remote_ref

if TYPE_CHECKING:
    from devops.context import BuildContext
    from devops.core.command import Command


# Anything that resolves (eventually) to a Target. Caller decides whether
# bare paths are also acceptable for its kwarg.
TargetSpec = Target | Ref


def resolve_target_spec(
    spec: TargetSpec,
    *,
    kwarg: str,
    ident: str,
) -> Target:
    """Lazy-resolve a Target / Ref to a concrete Target.

    Call this from ``build_cmds`` (not ``__init__``) so a Ref's
    network-backed resolution is deferred to when it's needed.

    Args:
        spec:    a Target or Ref
        kwarg:   the artifact kwarg this came from (e.g. ``"src"``,
                 ``"includes"``) — used in error messages only
        ident:   the artifact identifier (e.g.
                 ``"FileArtifact('foo')"``) — used in error messages only
    """
    if isinstance(spec, Target):
        return spec
    if isinstance(spec, Ref):
        return resolve_remote_ref(spec)
    raise TypeError(
        f"{ident}: {kwarg}={spec!r} cannot be resolved to a Target "
        f"(expected Target or Ref, got {type(spec).__name__})"
    )


# Process-global set of upstream targets already inlined this CLI run.
# Every artifact that consumes a Ref source goes through
# ``inline_ref_build_cmds``; this set ensures the same upstream's
# build_cmds aren't scheduled twice when multiple consumers reference
# the same Ref in a single ``devops`` invocation. Tests must call
# ``reset_ref_prelude_dedup`` between independent runs.
_INLINED_THIS_RUN: set[str] = set()


def reset_ref_prelude_dedup() -> None:
    """Clear the per-run dedup set. Called at the start of each CLI
    invocation and (via an autouse fixture) between tests."""
    _INLINED_THIS_RUN.clear()


def inline_ref_build_cmds(
    refs: Iterable[Ref], ctx: "BuildContext"
) -> list["Command"]:
    """Resolve each Ref and inline its upstream build_cmds, deduplicated.

    Dedup spans the whole CLI run via ``_INLINED_THIS_RUN`` so a single
    upstream referenced by multiple consumers in the same run is built
    once. Returns the prefix of commands that the calling artifact
    should emit before its own so the resolved upstream output exists
    on disk by the time the consumer reads it.
    """
    from devops.core.target import Artifact

    cmds: list[Command] = []
    for ref in refs:
        target = resolve_remote_ref(ref)
        if target.qualified_name in _INLINED_THIS_RUN:
            continue
        _INLINED_THIS_RUN.add(target.qualified_name)
        if isinstance(target, Artifact):
            cmds.extend(target.build_cmds(ctx))
    return cmds
