# Cross-compile profiles

Production binaries for a different CPU architecture; unit tests that
still run on your host. `devops` treats **arch** as first-class.

## How it works

- Every `Artifact` carries an `arch` attribute (default: `"host"`)
- `BuildContext.toolchains` is a dict keyed by arch name
- Every compile/link/ar invocation routes through
  `ctx.toolchain_for(target.arch)` so each target picks the right tool
- Test targets (`GoogleTest`, `Pytest`) force `arch="host"` regardless
  of what they're testing, so `devops test` runs on your machine

## Configuring toolchains

Put per-arch entries under `[toolchain.<arch>]` in your `devops.toml`:

```toml
# Host (default); keyword "host" is reserved.
[toolchain]
cc  = "clang"
cxx = "clang++"
ar  = "ar"

# Cross-compile for aarch64 Linux
[toolchain.aarch64]
cc  = ["aarch64-linux-gnu-gcc"]
cxx = ["aarch64-linux-gnu-g++"]
ar  = "aarch64-linux-gnu-ar"

# Cross-compile via a Docker-pinned toolchain
[toolchain.ppc64le]
cc  = ["docker", "run", "--rm",
       "-v", "{workspace}:{workspace}", "-w", "{cwd}",
       "ghcr.io/acme/ppc64-toolchain:v2", "powerpc64le-linux-gnu-gcc"]
cxx = ["docker", "run", "--rm",
       "-v", "{workspace}:{workspace}", "-w", "{cwd}",
       "ghcr.io/acme/ppc64-toolchain:v2", "powerpc64le-linux-gnu-g++"]
ar  = ["docker", "run", "--rm",
       "-v", "{workspace}:{workspace}", "-w", "{cwd}",
       "ghcr.io/acme/ppc64-toolchain:v2", "powerpc64le-linux-gnu-ar"]
```

## Declaring a target's arch

```python
# build.py
ElfBinary(
    name="embedded_app",
    srcs=glob("src/*.c"),
    arch="aarch64",                 # cross-compile
)

ElfBinary(
    name="embedded_app_host",
    srcs=glob("src/*.c"),
    # arch defaults to "host"; useful for local smoke tests
)
```

A single source tree can register the same underlying sources under two
arch-specific target names. Output trees are separate:

```
build/
├── Debug/
│   ├── host/<project>/embedded_app_host/
│   └── aarch64/<project>/embedded_app/
```

## Tests stay on host

GoogleTest and Pytest force `arch="host"` internally. If your production
library is declared `arch="aarch64"`, its unit test binary still builds
+ runs locally:

```python
aarch = StaticLibrary(name="core", srcs=glob("src/*.c"), arch="aarch64")

# The test target inherits core's flags + defines, but compiles its own
# sources with the host cxx. It won't link the cross-built archive
# (that's the wrong arch); it re-compiles core's sources for host.
GoogleTest(
    name="coreTests",
    srcs=glob("tests/*.cc"),
    target=aarch,
)
```

The result: `devops build core` produces an aarch64 binary,
`devops test coreTests` runs on your laptop.

## CLI

`devops` always uses the target's declared arch:

```bash
devops build embedded_app      # uses aarch64 (from target's arch=)
devops test                    # every test runs on host
devops describe                # prints each target's arch
```

## When NOT to use cross-compile profiles

If the **whole** workspace needs to be built for one non-host arch (e.g.
a purpose-built embedded project), it's usually simpler to set your host
toolchain to that cross-compiler directly and skip the `[toolchain.*]`
subtables. Cross-compile profiles are for the common case: *this* binary
runs on hardware, *that* binary runs on my laptop.
