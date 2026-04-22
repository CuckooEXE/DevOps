"""Version resolution: git describe → VERSION file → fallback."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git_describe(cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


def version_file(project_root: Path) -> str | None:
    path = project_root / "VERSION"
    if path.is_file():
        text = path.read_text().strip()
        return text or None
    return None


def resolve_version(project_root: Path, override: str | None) -> str:
    if override:
        return override
    for source in (git_describe, version_file):
        if v := source(project_root):
            return v
    return "0.0.0-unknown"
