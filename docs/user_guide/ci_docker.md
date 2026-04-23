# CI and Docker integration

The framework assumes nothing about where `devops` runs — local laptop,
cloud VM, ephemeral CI runner. For CI specifically, the idiomatic
pattern is **a per-project Docker image that layers on top of a shared
`devops` base image.** Each project image has exactly the tools that
project's `build.py` / `devops.toml` needs, pinned once at image-build
time.

## Shared base image

Published once and reused by every project in your org:

```dockerfile
# yourorg/devops-base:v1
FROM debian:13

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip sudo curl git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --break-system-packages \
      git+https://github.com/yourorg/devops.git@v1

WORKDIR /workspace
ENTRYPOINT ["devops"]
```

## Per-project image

Sits on top of the base and defers to `[bootstrap]` in `devops.toml` for
the actual install list. The image build fails (not CI) if `devops
doctor` finds any target referencing an uninstalled tool:

```dockerfile
# ProjectA/Dockerfile
FROM yourorg/devops-base:v1

# Copy the config first so the bootstrap layer is cached aggressively —
# only invalidates when tool requirements change, not on every src edit.
COPY devops.toml ./
RUN devops bootstrap

# Now validate against the target graph. `build.py` references globs
# that may want sources, but doctor only needs the top-level graph —
# copy just what's needed so image rebuilds stay fast.
COPY build.py ./
RUN devops doctor
```

The project's tool list lives in `devops.toml` under `[bootstrap]` (see
{doc}`bootstrap`). Adding a new `CustomArtifact` that needs a tool?
Update `devops.toml`, bump the image tag, CI picks up the new image on
next run. **The `devops.toml` is the single source of truth** —
Dockerfile is just a thin wrapper.

Build and tag:

```bash
docker build -t ghcr.io/yourorg/projecta-ci:v12 .
docker push ghcr.io/yourorg/projecta-ci:v12
```

## CI pipeline

One-liner invocation. Bake the tag into the workflow so stale images
don't silently succeed:

```yaml
# .github/workflows/build.yml
name: build
on: [push, pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    container: ghcr.io/yourorg/projecta-ci:v12
    steps:
      - uses: actions/checkout@v4
      - run: devops doctor           # fails fast if image drifted
      - run: devops lint
      - run: devops build MyCoolApp
      - run: devops test
```

Or without `container:` support:

```yaml
- uses: actions/checkout@v4
- run: |
    docker run --rm \
      -v ${{ github.workspace }}:/workspace -w /workspace \
      ghcr.io/yourorg/projecta-ci:v12 \
      bash -c 'devops doctor && devops lint && devops build MyCoolApp && devops test'
```

## Handling `CustomArtifact` tools

When a developer adds a `CustomArtifact(cmds=["protoc --python_out=..."])`,
the framework can't auto-detect `protoc` because shell commands hide
their executables. Two belt-and-suspenders:

1. **`required_tools=["protoc"]`** on the CustomArtifact — makes the
   tool visible to `devops doctor`.
2. **`devops doctor` runs at image-build time** — the project
   Dockerfile refuses to build until `protoc` is installed.

Result: "image built" is a stronger guarantee than "CI passed before"
— the image itself is the contract.

## When you don't want Docker

For cloud VMs / local laptops, the same `[bootstrap]` section drives
install directly on the host:

```bash
# on a fresh cloud VM / dev laptop
pip install --user git+https://github.com/yourorg/devops.git@v1
git clone https://github.com/yourorg/projectA.git
cd projectA
devops bootstrap            # reads [bootstrap] from devops.toml, installs everything
devops doctor               # sanity gate
devops build MyCoolApp
```

Same two commands as the Dockerfile, same source of truth.
