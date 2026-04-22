"""Resolve ``<url>::<target-name>`` references to targets in external projects.

Supported URL schemes:

=================  =========================================================
Scheme             Behaviour
=================  =========================================================
``file://``        Local path. A directory is copied (or rather, registered
                   in place). A ``.tar.gz`` / ``.tgz`` / ``.tar`` archive is
                   extracted. Supports absolute and relative paths.
``git+ssh://``     Git clone over SSH. Optional ``@<ref>`` suffix (branch,
                   tag, or sha) selects a revision after clone.
``http(s)://``     Download the URL as a tarball and extract.
=================  =========================================================

Resolved content is cached under ``~/.cache/devops/remotes/<hash>/``. Remove
the cache dir to force a re-fetch. Each remote is treated as its own
``Project`` — its ``build.py`` is imported and its registered targets become
addressable under the remote's project name.

Full spec form::

    <url>[@<ref>]::<TargetName>

    git+ssh://git@github.com/acme/libfoo@v1.2.3::mylib
    file:///abs/path/to/project::mylib
    file://./rel/project.tar.gz::mylib
    https://example.com/releases/libfoo-1.0.tar.gz::mylib

Resolution happens lazily during link-time (inside ``_link_flags_for_libs``),
so no network traffic occurs at build.py import time.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from devops import registry

if TYPE_CHECKING:
    from devops.core.target import Target


CACHE_ROOT = Path.home() / ".cache" / "devops" / "remotes"


# In-process cache: url -> project_name, so repeated references to the same
# remote during a single CLI invocation don't re-import the build.py.
_resolved: dict[str, str] = {}


def resolve_remote_ref(spec: str) -> "Target":
    """Parse ``<url>[@<ref>]::<Target>`` and return the referenced target."""
    if "::" not in spec:
        raise ValueError(
            f"remote ref must end with '::<target-name>', got {spec!r}"
        )
    url, target_name = spec.rsplit("::", 1)
    if not target_name:
        raise ValueError(f"remote ref missing target name: {spec!r}")

    if url in _resolved:
        project_name = _resolved[url]
    else:
        local_dir = _fetch(url)
        project_name = _register_remote_project(url, local_dir)
        _resolved[url] = project_name

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


def _register_remote_project(url: str, local_dir: Path) -> str:
    from devops.core.target import Project
    from devops.workspace import _load_build_py

    key = _cache_key(url)
    project_name = _project_name_for(url, key)
    proj = Project(name=project_name, root=local_dir.resolve())
    build_py = local_dir / "build.py"
    if not build_py.is_file():
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
