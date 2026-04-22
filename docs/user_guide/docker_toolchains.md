# Docker-wrapped toolchains

Lots of teams run their compilers inside Docker — to pin the toolchain
version, to isolate from the host, or because the target architecture
isn't easily installable on the developer machine. `devops` treats each
tool invocation as an **argv prefix** so this case is first-class.

## Configuring a tool

Put the argv prefix in `devops.toml` at the workspace root:

```toml
[toolchain]
cc = [
    "docker", "run", "--rm",
    "-v", "{workspace}:{workspace}",
    "-w", "{cwd}",
    "ghcr.io/acme/toolchain:v3",
    "clang",
]

cxx = [
    "docker", "run", "--rm",
    "-v", "{workspace}:{workspace}",
    "-w", "{cwd}",
    "ghcr.io/acme/toolchain:v3",
    "clang++",
]

clang_tidy = [
    "docker", "run", "--rm",
    "-v", "{workspace}:{workspace}",
    "-w", "{cwd}",
    "ghcr.io/acme/toolchain:v3",
    "clang-tidy",
]
```

When `devops build` runs, each compile/lint invocation is prefixed with
these tokens verbatim. A compile that would normally run as:

```
clang -O0 -ggdb -c main.c -o main.o
```

instead runs as:

```
docker run --rm -v /home/ada/proj:/home/ada/proj -w /home/ada/proj \
    ghcr.io/acme/toolchain:v3 clang -O0 -ggdb -c main.c -o main.o
```

## Placeholder substitution

Three placeholders are expanded per-Command:

| Placeholder    | Value                                         |
| -------------- | --------------------------------------------- |
| `{workspace}`  | Absolute workspace root                       |
| `{project}`    | Absolute project root (dir containing build.py) |
| `{cwd}`        | Command's cwd (falls back to project root)    |

## Bind-mount recommendation

Mount the workspace at the **same path** inside the container
(`-v {workspace}:{workspace}`). The framework uses absolute host paths
for `-I`, `-o`, `-c`, etc. — if host and container see the same paths,
everything Just Works with no translation layer.

If you mount at a different path, the compile commands inside the
container won't find inputs, and you'll need a path-translation layer
(not in the MVP).

## Tools that can be wrapped

Every tool the framework invokes is configurable:

- `cc`, `cxx`, `ar` — C/C++ compilation and archiving
- `clang_tidy`, `clang_format`, `cppcheck` — C/C++ lint
- `black`, `ruff` — Python lint
- `sphinx_build` — docs
- `pytest` — Python testing
- `python` — wheel building

Unknown keys in `[toolchain]` raise an error so typos surface
immediately.
