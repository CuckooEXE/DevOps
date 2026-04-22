"""Workspace-level build.py — builds the framework's own docs and exposes
a ``tests`` target that runs pytest with coverage.

From the repo root:
    devops build docs        # -> build/Debug/host/DevOps/docs/html/
    devops run tests         # run pytest + coverage report
    devops run tests-html    # coverage report to htmlcov/
    devops describe
"""

from builder import Script, SphinxDocs, glob


SphinxDocs(
    name="docs",
    srcs=glob("docs/**/*"),
    conf="docs",
    doc="User + developer docs for the devops build system itself.",
)

Script(
    name="tests",
    cmds=["python3 -m pytest tests/ --cov -q"],
    required_tools=["python3", "pytest"],
    doc="Run the framework's pytest suite with a coverage summary.",
)

Script(
    name="tests-html",
    cmds=["python3 -m pytest tests/ --cov --cov-report=html -q"],
    required_tools=["python3", "pytest"],
    doc="Run tests and emit a browseable coverage report under htmlcov/.",
)

Script(
    name="typecheck",
    cmds=["python3 -m mypy --strict devops/ builder/"],
    required_tools=["python3", "mypy"],
    doc="Run mypy --strict over the framework source tree.",
)
