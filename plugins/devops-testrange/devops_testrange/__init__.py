"""devops plugin: ``TestRangeTest``.

Runs libvirt-backed end-to-end tests via the
`testrange <https://pypi.org/project/testrange/>`_ pip package. Import
in a consuming project's ``build.py`` via::

    from builder.plugins import TestRangeTest

The ``testrange`` CLI itself is looked up via
``ctx.toolchain_for(arch).extras["testrange"]`` — this plugin seeds
a default pointing at a bare ``testrange`` on PATH. Override in
``devops.toml`` when you need to wrap it (e.g. inside a
libvirt-equipped container)::

    [toolchain.extras]
    testrange = ["docker", "run", "--rm",
                 "-v", "{workspace}:{workspace}",
                 "-w", "{cwd}",
                 "--device", "/dev/kvm",
                 "ghcr.io/acme/testrange:v1",
                 "testrange"]
"""

from __future__ import annotations

from pathlib import Path

from devops.api import BuildContext, Command, Target, Tool

# Hard dep on the devops TestTarget base class to opt into `devops test`
# selection. Imported from devops.targets.tests to stay in sync with
# core's __test__ = False marker and anything else that base class
# adds — plugins should not re-implement core machinery.
from devops.targets.tests import TestTarget, _resolve_sources


MIN_API_VERSION = "1"


class TestRangeTest(TestTarget):
    """Run libvirt-backed e2e tests via the ``testrange`` pip package.

    Each ``srcs`` entry is a Python file exposing a ``gen_tests``
    factory (override with ``factory=``). At ``devops test`` time we
    invoke ``testrange run <src>:<factory>`` once per src.

    ``artifacts`` maps stable aliases to built Targets. Each alias
    materializes as a ``DEVOPS_ARTIFACT_<ALIAS>`` env var on the
    ``testrange`` invocation, so the test function can discover
    built-binary paths without hardcoding them::

        artifacts={"app": myBinary}   →   os.environ["DEVOPS_ARTIFACT_APP"]

    Every artifact flows into ``self.deps`` so topo-sort builds them
    before the test runs.
    """

    def __init__(
        self,
        name: str,
        srcs,
        artifacts: dict[str, Target] | None = None,
        factory: str = "gen_tests",
        env: dict[str, str] | None = None,
        deps: dict[str, Target] | None = None,
        version: str | None = None,
        doc: str | None = None,
    ) -> None:
        super().__init__(name=name, deps=deps, version=version, doc=doc)
        self.srcs = _resolve_sources(self.project.root, srcs)
        self._artifacts: dict[str, Target] = dict(artifacts or {})
        self.factory = factory
        self._env = dict(env or {})
        for alias, artifact in self._artifacts.items():
            self.deps[f"_artifact_{alias}"] = artifact

    def output_path(self, ctx: BuildContext) -> Path:
        return self.output_dir(ctx) / ".testrange_stamp"

    def build_cmds(self, ctx: BuildContext) -> list[Command]:
        return []  # testrange is global; referenced artifacts build via deps

    def test_cmds(self, ctx: BuildContext) -> list[Command]:
        extras = ctx.toolchain_for(self.arch).extras
        if "testrange" not in extras:
            raise RuntimeError(
                f"TestRangeTest {self.name!r}: no 'testrange' tool "
                f"configured on toolchain for arch={self.arch!r}. "
                f"Install the devops-testrange plugin or add "
                f"[toolchain.extras]\\ntestrange = \"testrange\" to devops.toml."
            )
        testrange = extras["testrange"].resolved_for(
            workspace=ctx.workspace_root,
            project=self.project.root,
            cwd=self.project.root,
        )
        env: list[tuple[str, str]] = []
        artifact_inputs: list[Path] = []
        for alias, artifact in self._artifacts.items():
            out = artifact.output_path(ctx)
            env.append((f"DEVOPS_ARTIFACT_{alias.upper()}", str(out)))
            artifact_inputs.append(out)
        for k, v in self._env.items():
            env.append((k, v))
        env_tuple = tuple(env)
        return [
            Command(
                argv=testrange.invoke(["run", f"{src}:{self.factory}"]),
                cwd=self.project.root,
                env=env_tuple,
                label=f"testrange {self.name} / {src.name}",
                inputs=(src, *artifact_inputs),
            )
            for src in self.srcs
        ]

    def describe(self) -> str:
        if self._artifacts:
            alias_list = ", ".join(
                f"{alias}={a.qualified_name}"
                for alias, a in self._artifacts.items()
            )
        else:
            alias_list = "-"
        return (
            f"TestRangeTest {self.qualified_name}\n"
            f"  srcs:      {', '.join(s.name for s in self.srcs)}\n"
            f"  factory:   {self.factory}\n"
            f"  artifacts: {alias_list}"
        )


def register(api) -> None:
    """Entry-point hook called once at ``builder`` import time."""
    api.register_target(TestRangeTest)
    api.DEFAULT_TOOLCHAIN_EXTRAS.setdefault("testrange", Tool.of("testrange"))
