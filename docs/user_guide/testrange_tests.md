# TestRange e2e tests

`TestRangeTest` runs libvirt-backed end-to-end tests via the
[`testrange`](https://pypi.org/project/testrange/) pip package. Use it
when you need a full VM to exercise a built artifact — installer
smoke tests, agent rollouts, service-on-service integration. Use
`Pytest` or `GoogleTest` for unit-level checks.

`testrange` is treated as a global **tool** (like `pytest`); `devops`
doesn't manage a venv for it. Install once per box: `pip install
testrange`.

## Minimal example

```python
# build.py
from builder import ElfBinary, TestRangeTest, glob

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

`devops` builds `myApp` (via topo-sort from the `artifacts=` dep), then
invokes `testrange run <src>:gen_tests` once per file in `srcs`.

## The artifact contract

Every entry in `artifacts=` becomes an env var on the `testrange`
invocation: `artifacts={"alias": target}` →
`DEVOPS_ARTIFACT_<ALIAS>=<target.output_path>`. Keys are upper-cased,
and the alias is **yours to choose** — renaming the underlying Target
doesn't churn the env var name, which keeps the test code stable.

Artifacts also flow into the test target's `deps`, so `devops test`
builds them before the test runs.

## Kwargs

| Kwarg        | Default       | Meaning |
|--------------|---------------|---------|
| `name`       | required      | target identifier |
| `srcs`       | required      | Python files, each with a `gen_tests` factory |
| `artifacts`  | `None`        | `dict[str, Target]` — alias → built artifact |
| `factory`    | `"gen_tests"` | factory function name inside each src |
| `env`        | `None`        | extra env vars passed to `testrange` (e.g. `TESTRANGE_CACHE_DIR`) |
| `deps`       | `None`        | explicit Target deps beyond `artifacts` |
| `doc`        | `None`        | shown under `devops describe` |

Multiple srcs run as independent `testrange` invocations (one `Command`
each); any non-zero exit fails the whole target.

## Overriding `testrange` in `devops.toml`

Same as any Tool entry. Useful if your team runs `testrange` inside a
container that wraps libvirt:

```toml
[toolchain]
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
  `$PATH`) will fail the command — that's a real configuration error,
  not a test regression. Gate such runs on CI machines that have the
  backing infra.
- The first `testrange run` for a fresh VM spec boots the VM and
  snapshots the post-install disk into `/var/tmp/testrange/<user>/` —
  minutes. Subsequent runs hit that cache and finish in seconds. See
  the [`testrange` docs](https://testrange.readthedocs.io/) for cache
  tuning.
- `TestRangeTest` emits no `build_cmds()` of its own; artifacts build
  via `deps`, and the tool itself is assumed ready.
