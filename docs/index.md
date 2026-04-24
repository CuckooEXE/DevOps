# devops

A Python-defined, multi-language build system. You describe each project's
artifacts and scripts in a `build.py`; a single `devops` CLI drives
build/run/lint/test/describe/version across the workspace.

The design goal: flexibility that Make/CMake/Bazel/Meson trade off — with
first-class support for custom Docker-wrapped toolchains, linting that
reuses the compile flag vector automatically, and an extension story that
is just a subclass away.

---

## For users

You have a project written in C, C++, Python, or a mix, and you want to
build, lint, test, and ship it without hand-rolling a Makefile.

```{toctree}
:maxdepth: 2
:caption: User guide

user_guide/getting_started
user_guide/build_py
user_guide/target_types
user_guide/scripts
user_guide/testing
user_guide/testrange_tests
user_guide/docker_toolchains
user_guide/cross_compile
user_guide/remote_refs
user_guide/remote_run
user_guide/install
user_guide/bootstrap
user_guide/ci_docker
user_guide/cli
user_guide/graph
user_guide/watch
```

## For framework developers

You want to extend the framework with a new target type, a new lint tool,
or just understand the moving parts.

```{toctree}
:maxdepth: 2
:caption: Developer guide

developer_guide/architecture
developer_guide/adding_a_target_type
developer_guide/adding_a_tool
developer_guide/testing_the_framework
```
