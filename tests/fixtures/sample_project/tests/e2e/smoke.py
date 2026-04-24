"""Sample end-to-end test for MyCoolApp.

Demonstrates the DevOps↔TestRange plumbing:
  * Each entry in ``artifacts=`` on the TestRangeTest becomes a
    ``DEVOPS_ARTIFACT_<ALIAS>`` env var (alias, not Target.name — so
    this test code doesn't churn when targets get renamed).
  * The host paths point at fresh build outputs; topo-sort already
    built them.

This stub uploads the artifacts and asserts the binary is present +
executable. It deliberately does NOT exec the binary, because
MyCoolApp's link step bakes in host-absolute rpaths for
``libMyCoolLib.so`` and a vendored ``libgreetRemote.so`` — those
paths don't exist inside the VM, so a stock Debian box can't run the
binary without a proper deployment recipe (LD_LIBRARY_PATH, ldconfig,
or static linking). A real test would pick one of those and extend
this factory. Here we're showing the integration, not a full deploy.

Requires libvirt + the `testrange` pip package to actually execute.
"""

from __future__ import annotations

import os


def gen_tests():  # -> list[Test]
    # Deferred import so merely loading this file (lint, describe)
    # doesn't require testrange installed.
    from testrange import (  # type: ignore[import-not-found]
        VM, Orchestrator, Test, VirtualNetwork, VirtualNetworkRef,
        Credential, vCPU, Memory, HardDrive,
    )

    app_bin = os.environ["DEVOPS_ARTIFACT_APP"]
    mylib_so = os.environ["DEVOPS_ARTIFACT_MYLIB"]

    def smoke(orch):
        vm = orch.vms["web"]

        # Upload both artifacts. Aliases come from build.py's
        # artifacts={"app": ..., "mylib": ...}.
        vm.upload(app_bin, "/usr/local/bin/mycoolapp")
        vm.upload(mylib_so, "/usr/local/lib/libMyCoolLib.so")
        vm.exec(["chmod", "+x", "/usr/local/bin/mycoolapp"]).check()

        # Verify the binary was staged. Not executing it — see module
        # docstring for why.
        r = vm.exec(["file", "/usr/local/bin/mycoolapp"])
        r.check()
        assert b"ELF" in r.stdout, r.stdout_text

    return [
        Test(
            Orchestrator(
                networks=[VirtualNetwork("Net", "10.55.0.0/24", internet=True)],
                vms=[
                    VM(
                        name="web",
                        iso="https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2",
                        users=[Credential("root", "testrange")],
                        devices=[
                            vCPU(1), Memory(1), HardDrive(10),
                            VirtualNetworkRef("Net"),
                        ],
                    ),
                ],
            ),
            smoke,
            name="mycoolapp-smoke",
        ),
    ]
