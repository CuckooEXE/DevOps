"""Internal validators shared across artifact targets.

Centralized so FileArtifact.dest, DirectoryArtifact.dest, and
CompressedArtifact archive-path keys all reject the same shapes
(absolute paths, ``..`` segments, empty strings) with the same message.
"""

from __future__ import annotations

import re
from pathlib import Path

_OCTAL_MODE_RE = re.compile(r"[0-7]{3,4}")


def validate_relative_path(value: str, kwarg: str, target: str) -> None:
    """Reject empty / absolute / dot-dot path values.

    Args:
        value:   the raw path string to check
        kwarg:   keyword name shown in the error (e.g. ``"dest"``)
        target:  caller identifier shown in the error (e.g.
                 ``"FileArtifact('foo')"``)
    """
    if not value:
        raise ValueError(f"{target}: {kwarg}= must be non-empty")
    p = Path(value)
    if p.is_absolute():
        raise ValueError(f"{target}: {kwarg}= must be relative, got {value!r}")
    if any(part == ".." for part in p.parts):
        raise ValueError(
            f"{target}: {kwarg}= must not contain '..', got {value!r}"
        )


def validate_octal_mode(value: str | None, kwarg: str, target: str) -> None:
    """Accept ``None`` or a 3-4 digit octal string (e.g. ``"0755"``, ``"644"``)."""
    if value is None:
        return
    if not _OCTAL_MODE_RE.fullmatch(value):
        raise ValueError(
            f"{target}: {kwarg}={value!r} must be a 3- or 4-digit octal "
            f"string (e.g. '0755' or '755')"
        )
