"""Workspace-level build.py — builds the framework's own docs via its own
SphinxDocs target. Meta, but that's the point.

From the repo root:
    devops build docs        # -> build/Debug/DevOps/docs/html/
    devops describe
"""

from builder import SphinxDocs, glob


SphinxDocs(
    name="docs",
    srcs=glob("docs/**/*"),
    conf="docs",
    doc="User + developer docs for the devops build system itself.",
)
