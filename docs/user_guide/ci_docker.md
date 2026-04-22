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

Sits on top of the base, adds this project's tools, and validates with
`devops doctor` at image-build time — the image refuses to build if
`devops.toml`/`build.py` reference anything not installed:

```dockerfile
# ProjectA/Dockerfile
FROM yourorg/devops-base:v1

RUN apt-get update && apt-get install -y --no-install-recommends \
      clang-19 clang-tidy-19 clang-format-19 cppcheck \
      libgtest-dev \
    && rm -rf /var/lib/apt/lists/* \
 && ln -sf /usr/bin/clang-19 /usr/local/bin/clang \
 && ln -sf /usr/bin/clang++-19 /usr/local/bin/clang++ \
 && ln -sf /usr/bin/clang-tidy-19 /usr/local/bin/clang-tidy \
 && ln -sf /usr/bin/clang-format-19 /usr/local/bin/clang-format

RUN pip install --break-system-packages --no-cache-dir \
      ruff black shiv sphinx furo build pytest typer

# Vendor SDK — anything apt/pip can't handle
COPY ci/vendor-sdk.tar.gz /tmp/
RUN tar xz -f /tmp/vendor-sdk.tar.gz -C /opt/ && rm /tmp/vendor-sdk.tar.gz

# Image-build-time sanity gate: fails if any target needs a tool we
# haven't installed. Copy only what doctor needs (toml + build files +
# sources referenced by glob()s).
COPY devops.toml build.py ./
RUN devops doctor
```

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

For cloud VMs / local laptops, lean on the shared base image too:
`pip install` the framework, then run `apt-get install` manually for
the tools `devops doctor` reports as missing. The doctor's output is
designed to be readable and list-shaped so you can feed it straight
into an apt command.
