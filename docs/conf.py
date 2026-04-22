"""Sphinx configuration for the devops build system docs."""

from __future__ import annotations

project = "devops"
author = "the team"
copyright = f"%Y, {author}"  # noqa: A001

extensions = [
    "myst_parser",              # markdown support
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# ---- HTML (Furo) ----
html_theme = "furo"
html_title = "devops build system"
html_static_path: list[str] = []
html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
}

# ---- Cross-project references ----
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

# ---- MyST options: allow ```{directive} blocks, use `word`{role} syntax ----
myst_enable_extensions = [
    "colon_fence",
    "deflist",
]
