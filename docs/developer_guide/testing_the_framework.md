# Testing the framework

Two layers of tests ship:

1. **Unit tests** under `tests/test_*.py` — pure Python, no compilers
   required, run in fractions of a second.
2. **Integration / fixture** — the sample project under
   `tests/fixtures/sample_project/` exercises every MVP target type
   end-to-end and relies on clang, gtest, pytest, sphinx-build being
   available.

## Running the unit suite

From the repo root:

```bash
pytest
# or: python3 -m pytest tests/ -q
```

## Coverage

`pytest-cov` is wired via `pyproject.toml` — a coverage report prints
under the test output automatically:

```bash
pytest --cov                  # summary with per-file coverage
pytest --cov --cov-report=html    # browseable htmlcov/index.html
```

Coverage config lives under `[tool.coverage.run]` + `[tool.coverage.report]`
in `pyproject.toml`. Current baseline is ~85% with branch coverage
enabled; new code should keep the overall number from dropping.

The suite covers:

- `test_tool.py` — `Tool` argv expansion + placeholder substitution
- `test_glob.py` — `builder.glob()` semantics (exclude, allow_empty)
- `test_flag_composition.py` — C/C++ compile flags + lint reuses them
- `test_graph_script.py` — topo sort, cycle detection, Script
  templating
- `test_version.py` — version fallback chain
- `test_workspace.py` — workspace discovery + name resolution
- `test_targets_c_cpp.py` — every C/C++ artifact type + subclassing
- `test_targets_tests.py` — GoogleTest/Pytest + `tests=` sugar
- `test_targets_python_docs.py` — PythonWheel + SphinxDocs command
  shape
- `test_runner_cache.py` — dry-run, ToolMissing, CommandFailed, cache
  invalidation
- `test_cli.py` — CLI subcommands via `typer.testing.CliRunner`
- `test_doc.py` — `Target.doc` normalisation via `inspect.cleandoc`
- `test_script.py` — Script inline/file forms + deps templating

### The `tmp_project` fixture

`tests/conftest.py` provides a `tmp_project` fixture that hands back a
`(Project, enter_ctx)` pair. Use it to spin up an ephemeral project for
registering targets against:

```python
def test_my_thing(tmp_project, tmp_path):
    _, enter = tmp_project
    with enter():
        b = ElfBinary(name="x", srcs=[tmp_path / "main.c"])
    # ... assertions on b.build_cmds(ctx), etc.
```

The registry is reset automatically between tests (see the autouse
`_fresh_registry` fixture).

## Type checking

```bash
mypy --strict devops/ builder/
```

The whole package is mypy-strict clean. If your change breaks that, fix
it before landing — treat mypy as a test.

## Running the fixture end-to-end

```bash
cd tests/fixtures/sample_project
devops describe             # lists every target type
devops build MyCoolApp
devops run MyCoolApp
devops test                 # GoogleTests + Pytest
devops lint                 # graceful on missing tools
devops clean
```

Anything unexpected there is a regression — add a unit test covering the
affected path, then fix.

## Coverage of new features

When you add a new target type, tool, or CLI behaviour:

- [ ] Unit test for the command shape (golden argv fragment)
- [ ] Unit test for the error path (bad inputs raise typed errors)
- [ ] If it has user-visible output, a `test_cli.py` entry
- [ ] If it touches the fixture, update `sample_project/build.py` so
      the fixture keeps covering the full feature surface
