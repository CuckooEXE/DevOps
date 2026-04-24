"""Resolve typed remote references (``GitRef`` / ``TarballRef`` /
``DirectoryRef``) to targets in external projects.

Each Ref names an external project plus a target within it. The resolver
fetches the project, imports its ``build.py``, and returns the referenced
target. Fetched content is cached under ``~/.cache/devops/remotes/<hash>/``;
remove that directory to force a re-fetch.

================  ============================================================
Ref               Behaviour
================  ============================================================
``GitRef``        ``git clone`` over ssh / https / file. Optional ``ref=``
                  selects a branch, tag, or sha after clone.
``TarballRef``    Tarball at a local path or http(s) URL. Extracted on fetch.
                  Suffixes: ``.tar.gz`` / ``.tgz`` / ``.tar`` / ``.tar.xz`` /
                  ``.tar.bz2``.
``DirectoryRef``  Local directory (absolute or relative to cwd). Copied into
                  the cache.
================  ============================================================

Each remote is registered as its own ``Project``; its targets become
addressable under ``remote.<name>::<TargetName>``. Resolution happens
lazily at link-time, so no network traffic occurs at build.py import.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from devops import registry

if TYPE_CHECKING:
    from devops.core.target import Target


CACHE_ROOT = Path.home() / ".cache" / "devops" / "remotes"


# In-process cache: (url, build_override) -> project_name, so repeated
# references to the same remote during a single CLI invocation don't
# re-import the build.py. The override is part of the key so two Refs
# that share a source URL but apply different recipes register as two
# distinct projects instead of clobbering each other.
_resolved: dict[tuple[str, str | None], str] = {}


# ----- typed references ---------------------------------------------------
#
# Build files pass these instead of a raw "<scheme>://...::<target>" string
# so the intent (git clone vs. tarball vs. directory) is explicit at the
# call site and validated at construction. Each Ref lowers to the legacy
# spec string consumed by resolve_remote_ref via .to_spec().


class Ref:
    """Base class for remote refs. Subclasses carry their own fields."""

    target: str
    # Optional local build.py to apply to the fetched source instead of
    # the source's own build.py. Useful when depending on an external
    # project that wasn't built with devops — vendor a recipe and point
    # `build=` at it. Relative paths resolve against cwd at resolution
    # time; prefer absolute (e.g. ``str(Path(__file__).parent / "..."))``.
    build: str | Path | None

    def to_spec(self) -> str:
        """Lower this ref to the ``<url>::<target>`` string the resolver uses."""
        raise NotImplementedError


@dataclass(frozen=True)
class GitRef(Ref):
    """Git clone reference over ssh, https, or local file://.

    ``url`` is passed to ``git clone`` after prefixing with ``git+`` — supply
    the bare URL without ``git+`` (e.g. ``ssh://git@github.com/acme/libfoo``
    or ``https://github.com/acme/libfoo.git``). ``ref`` optionally selects
    a branch/tag/sha after clone. ``build`` optionally overrides the
    fetched project's build.py with a local recipe file.
    """

    url: str
    target: str
    ref: str | None = None
    build: str | Path | None = None

    def to_spec(self) -> str:
        url = f"{self.url}@{self.ref}" if self.ref is not None else self.url
        return f"git+{url}::{self.target}"


@dataclass(frozen=True)
class TarballRef(Ref):
    """Tarball reference — local file path or http(s) URL.

    Local paths (absolute or relative to cwd) are rewritten to ``file://``.
    Supported suffixes: ``.tar.gz``, ``.tgz``, ``.tar``, ``.tar.xz``,
    ``.tar.bz2``. ``build`` optionally overrides the extracted project's
    build.py with a local recipe file.
    """

    url: str
    target: str
    build: str | Path | None = None

    def to_spec(self) -> str:
        if "://" in self.url:
            return f"{self.url}::{self.target}"
        p = Path(self.url)
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        return f"file://{p}::{self.target}"


@dataclass(frozen=True)
class DirectoryRef(Ref):
    """Local directory reference — absolute or relative path.

    Relative paths resolve against the current working directory at
    resolution time. ``build`` optionally overrides the referenced
    project's build.py with a local recipe file.
    """

    path: str
    target: str
    build: str | Path | None = None

    def to_spec(self) -> str:
        p = Path(self.path)
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        return f"file://{p}::{self.target}"


def resolve_remote_ref(ref: Ref) -> "Target":
    """Fetch the project named by ``ref`` and return the referenced target."""
    if not isinstance(ref, Ref):
        raise TypeError(
            f"resolve_remote_ref expects a Ref "
            f"(GitRef / TarballRef / DirectoryRef); got {type(ref).__name__}"
        )
    if not ref.target:
        raise ValueError(f"ref is missing a target name: {ref!r}")
    spec = ref.to_spec()
    url, target_name = spec.rsplit("::", 1)

    build_override: Path | None = None
    if ref.build is not None:
        bp = Path(ref.build)
        if not bp.is_absolute():
            bp = (Path.cwd() / bp).resolve()
        build_override = bp

    cache_key = (url, str(build_override) if build_override else None)
    if cache_key in _resolved:
        project_name = _resolved[cache_key]
    else:
        local_dir = _fetch(url)
        project_name = _register_remote_project(
            url, local_dir, build_override=build_override
        )
        _resolved[cache_key] = project_name

    return registry.resolve(f"{project_name}::{target_name}")


# ----- cache key + register -----------------------------------------------


def _cache_key(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _project_name_for(url: str, key: str) -> str:
    """Derive a stable project name from the URL.

    Prefers the last path segment (trimmed of common suffixes) so the
    remote shows up as ``libfoo::mylib`` in ``devops describe`` rather than
    as an opaque hash.
    """
    parsed = urlparse(url)
    last = Path(parsed.path).name or "remote"
    for suffix in (".tar.gz", ".tgz", ".tar", ".git"):
        if last.endswith(suffix):
            last = last[: -len(suffix)]
    # Fallback if the URL trick didn't yield anything usable
    safe = last.strip("/@") or f"remote-{key[:8]}"
    return f"remote.{safe}"


def _register_remote_project(
    url: str,
    local_dir: Path,
    build_override: Path | None = None,
) -> str:
    from devops.core.target import Project
    from devops.workspace import _load_build_py

    key = _cache_key(url)
    project_name = _project_name_for(url, key)
    if build_override is not None:
        # Distinguish same URL + different recipe so registrations don't
        # collide. Suffix is short but stable per-recipe.
        recipe_key = hashlib.sha1(str(build_override).encode()).hexdigest()[:8]
        project_name = f"{project_name}.{recipe_key}"

    proj = Project(name=project_name, root=local_dir.resolve())
    build_py = build_override if build_override is not None else local_dir / "build.py"
    if not build_py.is_file():
        if build_override is not None:
            raise FileNotFoundError(
                f"remote {url!r}: build= recipe not found at {build_py}"
            )
        raise FileNotFoundError(
            f"remote {url!r}: expected build.py at {build_py}"
        )
    _load_build_py(build_py, proj)
    return project_name


# ----- fetch dispatch -----------------------------------------------------


def _fetch(url: str) -> Path:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    cached = CACHE_ROOT / _cache_key(url)
    if cached.is_dir() and any(cached.iterdir()):
        return cached

    # clear half-written cache dirs
    if cached.exists():
        shutil.rmtree(cached)

    scheme = urlparse(url).scheme
    if scheme == "file":
        _fetch_file(url, cached)
    elif scheme.startswith("git+"):
        _fetch_git(url, cached)
    elif scheme in ("http", "https"):
        _fetch_http(url, cached)
    else:
        raise ValueError(f"unsupported remote scheme: {scheme!r} (in {url!r})")
    return cached


# ----- file:// (directory or tarball) -------------------------------------


_TARBALL_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".tar.xz", ".tar.bz2")


def _fetch_file(url: str, target: Path) -> None:
    # file:///abs/path  -> path starts with /
    # file://./rel      -> path starts with ./  (or anything without a leading /)
    raw = url[len("file://"):]
    src = Path(raw)
    if not src.is_absolute():
        src = (Path.cwd() / src).resolve()

    if any(str(src).endswith(s) for s in _TARBALL_SUFFIXES):
        _extract_tarball(src, target)
    elif src.is_dir():
        shutil.copytree(src, target)
    else:
        raise ValueError(
            f"file:// must point to a dir or a supported tarball "
            f"({', '.join(_TARBALL_SUFFIXES)}); got {src}"
        )


# ----- git+ssh://[user@]host[:port]/path[@ref] ----------------------------


def _split_git_ref(git_url: str) -> tuple[str, str | None]:
    """Split a trailing ``@<ref>`` off a git URL, if present.

    The git URL itself may contain an ``@`` for user@host, so we only peel
    off a ``@`` that appears **after** the final ``/`` of the URL path.
    """
    last_slash = git_url.rfind("/")
    if last_slash == -1:
        return git_url, None
    tail = git_url[last_slash + 1:]
    if "@" in tail:
        ref_idx = tail.rfind("@")
        return git_url[: last_slash + 1] + tail[:ref_idx], tail[ref_idx + 1:]
    return git_url, None


def _fetch_git(url: str, target: Path) -> None:
    # strip the "git+" prefix — leaves an actual URL git understands
    git_url = url[len("git+"):]
    git_url, ref = _split_git_ref(git_url)

    argv = ["git", "clone", "--depth", "1"]
    if ref:
        argv.extend(["--branch", ref])
    argv.extend([git_url, str(target)])
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        # --branch may not accept a bare sha; retry as full clone + checkout
        if ref:
            shutil.rmtree(target, ignore_errors=True)
            subprocess.run(
                ["git", "clone", git_url, str(target)],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(target), "checkout", ref],
                check=True, capture_output=True,
            )
        else:
            raise RuntimeError(f"git clone failed: {result.stderr.strip()}")


# ----- http(s):// (tarball download) --------------------------------------


def _fetch_http(url: str, target: Path) -> None:
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        try:
            urllib.request.urlretrieve(url, tmp.name)  # noqa: S310 (user-provided URL)
            _extract_tarball(Path(tmp.name), target)
        finally:
            Path(tmp.name).unlink(missing_ok=True)


# ----- tarball extraction -------------------------------------------------


def _extract_tarball(src: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(src) as tar:
        # Python 3.12+ always exposes this; we require 3.11+ but still
        # fall back if the member filter is unavailable (older minor).
        try:
            tar.extractall(target, filter="data")
        except TypeError:
            tar.extractall(target)

    # Promote single top-level dir's contents if the tarball has a
    # project-name wrapper directory (typical github tarball shape).
    entries = list(target.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for c in inner.iterdir():
            shutil.move(str(c), str(target / c.name))
        inner.rmdir()


# ----- testing hook -------------------------------------------------------


def _reset_for_tests() -> None:
    """Clear the in-process URL→project_name cache. Tests only."""
    _resolved.clear()
