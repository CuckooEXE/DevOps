"""Install tools declared in devops.toml's [bootstrap] section.

The paired half of ``devops doctor``: where ``doctor`` tells you what's
missing, ``bootstrap`` installs it. Co-locating install logic with the
build graph (in the same ``devops.toml``) means dev laptops, fresh VMs,
and CI all run the same two commands:

    devops bootstrap     # apt-get / pip install / user-supplied run steps
    devops doctor        # sanity gate
    devops build ...

Schema::

    [bootstrap]
    apt = ["clang-19", "cppcheck"]        # ~> sudo apt-get install -y ...
    pip = ["ruff==0.8.2", "black"]        # ~> pip install [pip_args...] ...
    pip_args = ["--user"]                 # default: ["--user"]
    run = [                               # verbatim shell commands
        "sudo ln -sf /usr/bin/clang-19 /usr/local/bin/clang",
        "curl -sSL https://vendor.example.com/sdk.tar.gz | sudo tar xz -C /opt/",
    ]

Order of execution: ``apt`` → ``pip`` → ``run`` — each list runs as one
combined command where possible (single ``apt-get install``, single
``pip install``) so package-manager deduplication and caching do their
thing.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from devops.core.command import Command


DEFAULT_PIP_ARGS: tuple[str, ...] = ("--user",)


@dataclass
class BootstrapConfig:
    apt: tuple[str, ...] = ()
    pip: tuple[str, ...] = ()
    pip_args: tuple[str, ...] = DEFAULT_PIP_ARGS
    run: tuple[str, ...] = ()
    # Raw path the config was loaded from (useful for error messages)
    _source: Path | None = field(default=None, repr=False)

    @property
    def is_empty(self) -> bool:
        return not (self.apt or self.pip or self.run)


def load_bootstrap(workspace_root: Path) -> BootstrapConfig:
    """Read ``[bootstrap]`` from ``<workspace_root>/devops.toml``.

    Missing file or missing section yields an empty ``BootstrapConfig``.
    Unknown keys raise, so typos surface.
    """
    cfg_path = workspace_root / "devops.toml"
    if not cfg_path.is_file():
        return BootstrapConfig(_source=cfg_path)
    with cfg_path.open("rb") as f:
        data = tomllib.load(f)
    section = data.get("bootstrap")
    if not section:
        return BootstrapConfig(_source=cfg_path)

    known = {"apt", "pip", "pip_args", "run"}
    unknown = set(section) - known
    if unknown:
        raise ValueError(
            f"unknown [bootstrap] key(s) {sorted(unknown)} in {cfg_path}; "
            f"known keys: {sorted(known)}"
        )

    def _seq(key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
        val = section.get(key, default)
        if isinstance(val, str):
            return (val,)
        if not isinstance(val, (list, tuple)):
            raise TypeError(
                f"[bootstrap].{key} must be a list of strings, got {type(val).__name__}"
            )
        for i, v in enumerate(val):
            if not isinstance(v, str):
                raise TypeError(
                    f"[bootstrap].{key}[{i}] must be a string, got {type(v).__name__}"
                )
        return tuple(val)

    return BootstrapConfig(
        apt=_seq("apt"),
        pip=_seq("pip"),
        pip_args=_seq("pip_args", DEFAULT_PIP_ARGS),
        run=_seq("run"),
        _source=cfg_path,
    )


def bootstrap_commands(cfg: BootstrapConfig, cwd: Path) -> list[Command]:
    """Render the config into an ordered list of Commands to execute.

    Returns an empty list when the config has nothing to install — the
    caller decides whether to print a note or stay silent.
    """
    cmds: list[Command] = []

    if cfg.apt:
        cmds.append(
            Command(
                argv=("sudo", "apt-get", "update"),
                cwd=cwd,
                label="apt update",
            )
        )
        cmds.append(
            Command(
                argv=("sudo", "apt-get", "install", "-y", *cfg.apt),
                cwd=cwd,
                label=f"apt install ({len(cfg.apt)})",
            )
        )

    if cfg.pip:
        # Run via `python3 -m pip` so we don't depend on a `pip` shim being
        # on PATH (some fresh Debian installs only have `pip3` or nothing).
        cmds.append(
            Command(
                argv=("python3", "-m", "pip", "install", *cfg.pip_args, *cfg.pip),
                cwd=cwd,
                label=f"pip install ({len(cfg.pip)})",
            )
        )

    for i, line in enumerate(cfg.run):
        cmds.append(
            Command.shell_cmd(
                line,
                cwd=cwd,
                label=f"bootstrap.run[{i}]",
            )
        )

    return cmds
