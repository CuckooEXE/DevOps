# Bootstrapping a project's tool dependencies

`devops doctor` tells you **what's missing**; `devops bootstrap` tells
you **how to get it**. Together they close the "fresh-VM / fresh-CI
runner / fresh laptop" onboarding gap: one command installs everything
the project's targets need, declared alongside the build graph in the
same `devops.toml`.

## The schema

```toml
[bootstrap]
# System packages — installed via `sudo apt-get install -y …`
apt = ["clang-19", "clang-tidy-19", "cppcheck", "libgtest-dev"]

# Python packages — installed via `python3 -m pip install [pip_args] …`
pip = ["ruff==0.8.2", "black", "shiv", "sphinx"]

# Extra args to `pip install`. Default: ["--user"].
# Use ["--user", "--break-system-packages"] on Debian 13 / Ubuntu 24.04+
# where PEP 668 blocks system-site writes.
pip_args = ["--user", "--break-system-packages"]

# Anything apt / pip can't handle. Verbatim shell commands; run last.
run = [
    "sudo ln -sf /usr/bin/clang-19 /usr/local/bin/clang",
    "curl -sSL https://ziglang.org/download/0.15.1/zig-linux-x86_64-0.15.1.tar.xz | sudo tar xJ -C /opt/",
    "sudo ln -sf /opt/zig-linux-x86_64-0.15.1/zig /usr/local/bin/zig",
]
```

Order of execution: `apt` → `pip` → `run`. All three are optional.
Unknown keys raise — typos surface.

## The workflow

On any fresh environment (VM, Docker build step, dev laptop):

```bash
git clone …
devops bootstrap          # install everything declared
devops doctor             # sanity gate
devops build MyCoolApp
```

`doctor` picks up on `[bootstrap]` — if tools are missing, its error
message suggests `devops bootstrap` instead of the generic "install
them somehow" hint.

`bootstrap --dry-run` prints the commands without executing, useful
when you're copy-pasting into a Dockerfile or troubleshooting.

## Idempotency

- `apt-get install -y` is idempotent — already-installed packages are
  no-ops.
- `pip install` is mostly idempotent — already-satisfied versions are
  skipped, version bumps upgrade in place.
- `run` entries are whatever the user wrote. Prefer shell commands that
  tolerate being re-run (`ln -sf`, `mkdir -p`, `curl | tar` to a fresh
  dir, etc.).

Because of that, it's safe to run `devops bootstrap` unconditionally at
the top of CI jobs or Dockerfile layers.

## When `run` gets long

If `run` grows past ~5 lines, it's probably worth moving the imperative
logic into a shell script and calling it from here:

```toml
[bootstrap]
apt = ["curl"]
run = ["bash tools/bootstrap.sh"]
```

`tools/bootstrap.sh` can then use `set -euo pipefail`, trap handlers,
and other script hygiene `devops.toml` strings can't express.

## Inside a Dockerfile

Put `bootstrap` at the top of the project image so the tool-install
layer is cached:

```dockerfile
FROM yourorg/devops-base:v1

# Copy just the config so the layer below caches on toolchain changes,
# not every source tweak.
COPY devops.toml /tmp/proj/
WORKDIR /tmp/proj
RUN devops bootstrap

# Validate the image matches the target graph. Fails the image build
# (not CI) if build.py references a tool that isn't installed.
COPY build.py /tmp/proj/
RUN devops doctor

WORKDIR /workspace
```

See {doc}`ci_docker` for the full CI story.

## What `bootstrap` doesn't do

- It doesn't track state. Every `devops bootstrap` invocation re-runs
  every step. That's fine because apt/pip are idempotent; it's up to
  `run` entries to be re-runnable.
- It doesn't resolve version conflicts across projects. Pin in pip, pin
  in `apt` with version suffixes (`clang-19`, not `clang`), or bake
  into a Docker image version tag.
- It doesn't sandbox. `sudo` is used literally; commands run with
  whatever privileges the calling user has.
- It doesn't install `devops` itself — bootstrap the framework via
  `pip install` / a base Docker image / a `devops` wheel. Bootstrap
  handles the downstream tools only.
