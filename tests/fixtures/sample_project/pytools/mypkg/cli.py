"""Tiny CLI that prints mypkg.greet() — used by PythonApp / PythonShiv fixtures."""

from __future__ import annotations

import sys

from mypkg import greet


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "world"
    print(greet(name))
    return 0
