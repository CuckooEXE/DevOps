"""Helpers for resolving source/target specs at build time.

Most artifact kwargs accept some mix of:

    str / Path     — a literal filesystem path
    Target         — another build target (eager: caller holds the object)
    Ref            — a remote reference (GitRef / TarballRef / DirectoryRef);
                     resolves at build_cmds time via the network/cache layer

The ``ResolvedSource`` dataclass + ``coerce_source`` factory replace
the hand-rolled isinstance ladder + dep registration + lazy resolution
+ ref-prelude scheduling that each artifact (FileArtifact,
DirectoryArtifact, CompressedArtifact, CustomArtifact, Install) used
to spell out by hand.

Three primitives:

    coerce_source(value, *, kwarg, ident, project_root, accept_paths=...,
                  deps=..., dep_kind=..., dep_suffix=...)
        Validate-and-store a source spec at __init__ time. Returns a
        ResolvedSource. When dep_kind is provided and the value is an
        Artifact, registers it as a typed dep so topo-sort sees it.

    ResolvedSource.resolve(ctx, *, ident, kwarg) -> Path
        Materialize at build_cmds time: paths pass through; Target/Ref
        sources resolve to ``output_path(ctx)``.

    inline_ref_build_cmds(sources, ctx)
        Emit build commands for every Ref-typed source in `sources`
        ahead of the consuming artifact's own commands. Refs aren't in
        ``deps`` (resolution is network-backed), so topo-sort can't see
        them — the consumer schedules them itself. Dedup'd per CLI run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from devops.core.target import Artifact, DepKind, Target
from devops.remote import Ref, resolve_remote_ref

if TYPE_CHECKING:
    from devops.context import BuildContext
    from devops.core.command import Command


TargetSpec = Target | Ref


@dataclass(frozen=True)
class ResolvedSource:
    """A source spec parsed at __init__ time, resolved at build_cmds time.

    Exactly one of ``path`` / ``target`` / ``ref`` is set:

      - ``path`` for raw filesystem sources (resolved to absolute at
        coerce time so the cache key is stable).
      - ``target`` for an eager Target reference (caller holds the
        Python object).
      - ``ref`` for a deferred Ref (Git/Tarball/Directory). Resolution
        is network-backed and happens inside ``resolve``.

    The constructor isn't intended for direct use — go through
    ``coerce_source``.
    """

    path: Path | None = None
    target: Target | None = None
    ref: Ref | None = None

    @property
    def kind(self) -> str:
        if self.path is not None:
            return "path"
        if self.target is not None:
            return "target"
        return "ref"

    @property
    def is_ref(self) -> bool:
        return self.ref is not None

    def resolve(self, ctx: "BuildContext", *, kwarg: str, ident: str) -> Path:
        """Materialize the source's path under the given context.

        Path sources pass through. Target/Ref sources resolve to the
        upstream Artifact's ``output_path(ctx)`` (raises ``TypeError``
        if the resolved Target isn't an Artifact — only Artifacts have
        an output path to consume).
        """
        if self.path is not None:
            return self.path
        tgt = self.resolve_target()
        assert tgt is not None  # path branch handled above
        if not isinstance(tgt, Artifact):
            raise TypeError(
                f"{ident}: {kwarg} resolved to {type(tgt).__name__}, "
                f"expected an Artifact"
            )
        return tgt.output_path(ctx)

    def resolve_target(self) -> Target | None:
        """Return the upstream Target for Target/Ref sources, or None
        for Path sources. Resolves Refs (network-backed) when needed."""
        if self.target is not None:
            return self.target
        if self.ref is not None:
            return resolve_remote_ref(self.ref)
        return None

    def describe_str(self) -> str:
        """Human-readable rendering for ``Target.describe()`` output."""
        if self.path is not None:
            return str(self.path)
        if self.target is not None:
            return self.target.qualified_name
        assert self.ref is not None
        return self.ref.to_spec()


def coerce_source(
    value: "str | Path | Target | Ref",
    *,
    kwarg: str,
    ident: str,
    project_root: Path,
    accept_paths: bool = True,
    deps: dict[str, Target] | None = None,
    dep_kind: DepKind | None = None,
    dep_suffix: str | None = None,
) -> ResolvedSource:
    """Validate a source spec and return a ResolvedSource.

    When the value is an Artifact and both ``deps`` and ``dep_kind``
    are provided, registers the Artifact under that kind so topo-sort
    schedules it before the consumer. (The caller's own
    ``Target.register_dep`` call is replaced by passing
    ``deps=self.deps, dep_kind=DepKind.X`` here.)

    Args:
        value:        the raw source spec from the artifact kwarg
        kwarg:        keyword name shown in error messages (e.g. ``"src"``)
        ident:        artifact identifier shown in errors
                      (e.g. ``"FileArtifact('foo')"``)
        project_root: base for resolving relative path values
        accept_paths: when False, str/Path values are rejected — useful
                      for kwargs that only make sense with another
                      target's output (currently nothing uses this).
        deps:         the consuming Target's ``self.deps`` dict;
                      populated when value is an Artifact and
                      ``dep_kind`` is set.
        dep_kind:     DepKind to register under when value is an
                      Artifact. None to skip dep registration.
        dep_suffix:   suffix override for the dep key. Defaults to
                      ``target.name`` (see ``Target.register_dep``).
    """
    if isinstance(value, Artifact):
        if deps is not None and dep_kind is not None:
            suffix = dep_suffix if dep_suffix is not None else value.name
            deps[f"{dep_kind.prefix}{suffix}"] = value
        return ResolvedSource(target=value)
    if isinstance(value, Target):
        # Non-Artifact Target as a source is unusual but legal — the
        # caller may want to template against it without consuming an
        # output. Don't auto-register.
        return ResolvedSource(target=value)
    if isinstance(value, Ref):
        return ResolvedSource(ref=value)
    if isinstance(value, (str, Path)):
        if not accept_paths:
            raise TypeError(
                f"{ident}: {kwarg}={value!r} — this kwarg requires a "
                f"Target or Ref, not a filesystem path"
            )
        p = Path(value)
        if not p.is_absolute():
            p = (project_root / p).resolve()
        return ResolvedSource(path=p)
    raise TypeError(
        f"{ident}: {kwarg}={value!r} must be str, Path, Artifact, or Ref; "
        f"got {type(value).__name__}"
    )


# ---- Ref-prelude scheduling --------------------------------------------


def resolve_target_spec(
    spec: TargetSpec, *, kwarg: str, ident: str,
) -> Target:
    """Lazy-resolve a Target/Ref to a concrete Target.

    Retained for callers that don't go through ``ResolvedSource``
    (e.g. ``c_cpp._include_dir``, where the Target type is constrained
    to ``HeadersOnly`` after resolution). New artifacts should prefer
    ``ResolvedSource.resolve``.
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
    refs: Iterable[Ref], ctx: "BuildContext",
) -> list["Command"]:
    """Resolve each Ref and inline its upstream build_cmds, deduplicated.

    Dedup spans the whole CLI run via ``_INLINED_THIS_RUN`` so a single
    upstream referenced by multiple consumers in the same run is built
    once. Returns the prefix of commands that the calling artifact
    should emit before its own so the resolved upstream output exists
    on disk by the time the consumer reads it.

    Accepts a plain ``Iterable[Ref]`` (caller filters their source
    list); a convenience wrapper that filters ``ResolvedSource`` lists
    is ``ref_prelude_for``.
    """
    cmds: list[Command] = []
    for ref in refs:
        target = resolve_remote_ref(ref)
        if target.qualified_name in _INLINED_THIS_RUN:
            continue
        _INLINED_THIS_RUN.add(target.qualified_name)
        if isinstance(target, Artifact):
            cmds.extend(target.build_cmds(ctx))
    return cmds


def ref_prelude_for(
    sources: Iterable[ResolvedSource], ctx: "BuildContext",
) -> list["Command"]:
    """Inline build cmds for every Ref-typed entry in ``sources``."""
    return inline_ref_build_cmds(
        [s.ref for s in sources if s.ref is not None], ctx,
    )
