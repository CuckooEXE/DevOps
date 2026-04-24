# devops-testrange

A devops plugin that wraps the
[`testrange`](https://pypi.org/project/testrange/) pip package for
libvirt-backed end-to-end testing. Use it when you need a full VM to
exercise a built artifact — installer smoke tests, agent rollouts,
service-on-service integration — and `Pytest` / `GoogleTest` aren't
enough.

## Install

```sh
pip install -e ./plugins/devops-testrange
# or once published:
pip install devops-testrange
```

`testrange` itself is a separate package (`pip install testrange`)
and needs libvirt + KVM on the host. `devops doctor` flags it as
missing if it isn't on `$PATH`.

## Use

```python
# build.py
from builder import ElfBinary, glob
from builder.plugins import TestRangeTest

myApp = ElfBinary(name="MyApp", srcs=glob("src/*.c"))

TestRangeTest(
    name="MyAppE2E",
    srcs=glob("tests/e2e/*.py"),
    artifacts={"app": myApp},
    doc="Boot a VM, upload MyApp, exec it, check exit.",
)
```

```python
# tests/e2e/smoke.py
import os
from testrange import (
    VM, Orchestrator, Test, VirtualNetwork, VirtualNetworkRef,
    Credential, Apt, vCPU, Memory, HardDrive,
)

APP_BIN = os.environ["DEVOPS_ARTIFACT_APP"]   # host path to the built MyApp

def smoke(orch):
    vm = orch.vms["web"]
    vm.upload(APP_BIN, "/usr/local/bin/myapp")
    vm.exec(["chmod", "+x", "/usr/local/bin/myapp"]).check()
    vm.exec(["/usr/local/bin/myapp"]).check()

def gen_tests():
    return [Test(Orchestrator(...), smoke, name="smoke")]
```

Run:

```
devops test MyAppE2E
```

devops builds `myApp` via the topo-sort through `artifacts=`, then
invokes `testrange run <src>:gen_tests` once per file in `srcs`.

## Artifact contract

Every entry in `artifacts=` becomes an env var on the `testrange`
invocation: `artifacts={"alias": target}` →
`DEVOPS_ARTIFACT_<ALIAS>=<target.output_path>`. Keys are
upper-cased; the alias is **yours to choose** — renaming the
underlying Target doesn't churn the env var name, which keeps the
test code stable.

Artifacts also flow into `self.deps`, so `devops test` builds them
before the test runs.

## Kwargs

| Kwarg        | Default       | Meaning |
|--------------|---------------|---------|
| `name`       | required      | target identifier |
| `srcs`       | required      | Python files, each with a `gen_tests` factory |
| `artifacts`  | `None`        | `dict[str, Target]` — alias → built artifact |
| `factory`    | `"gen_tests"` | factory function name inside each src |
| `env`        | `None`        | extra env vars passed to `testrange` |
| `deps`       | `None`        | explicit Target deps beyond `artifacts` |
| `doc`        | `None`        | shown under `devops describe` |

Multiple srcs run as independent `testrange` invocations (one
`Command` each); any non-zero exit fails the whole target.

## Overriding `testrange` in `devops.toml`

Same as any Tool entry. Useful if your team runs `testrange` inside
a container that wraps libvirt:

```toml
[toolchain.extras]
testrange = [
    "docker", "run", "--rm",
    "-v", "{workspace}:{workspace}",
    "-w", "{cwd}",
    "--device", "/dev/kvm",
    "ghcr.io/acme/tr:v1",
    "testrange",
]
```

## Notes

- `devops test` on a box without libvirt (or without `testrange` on
  `$PATH`) fails the command — that's a real configuration error,
  not a test regression. Gate such runs on CI machines that have
  the backing infra.
- The first `testrange run` for a fresh VM spec boots the VM and
  snapshots the post-install disk into `/var/tmp/testrange/<user>/`
  — minutes. Subsequent runs hit that cache and finish in seconds.
- `TestRangeTest` emits no `build_cmds()` of its own; artifacts
  build via `deps`, and the tool itself is assumed ready.
