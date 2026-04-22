# Testing

`devops test` builds and runs every test target in the workspace (or a
selected subset). Two MVP test types ship:

- `GoogleTest` for C/C++
- `Pytest` for Python

## GoogleTest

Two ways to wire one up:

### Explicit form

```python
myLib = ElfSharedObject(name="MyLib", srcs=glob("src/*.c"))

GoogleTest(
    name="MyLibTests",
    srcs=glob("tests/*.cc"),
    target=myLib,
)
```

### Sugar

On any C-family artifact, `tests={...}` desugars to a sibling GoogleTest
named `<name>Tests`:

```python
ElfBinary(
    name="MyApp",
    srcs=glob("src/*.c"),
    libs=[myLib],
    tests={"srcs": glob("tests/*.cc")},
)
# -> auto-registers GoogleTest(name="MyAppTests", target=self, srcs=...)
```

In both forms, the test inherits the target's compile environment:
`flags`, `includes`, `defs`, `undefs`. If the target is a library, the
test links against it directly. If the target is an `ElfBinary`, the
test links everything the binary links — so tests see the same library
graph as production code.

Default libraries: `-lgtest -lgtest_main -lpthread`. Override with
`extra_libs=(...)`.

## Pytest

```python
PythonWheel(
    name="mypkg",
    pyproject="pyproject.toml",
    tests={"srcs": glob("tests/*.py")},
)
# -> auto-registers Pytest(name="mypkgTests", target=self)
```

The generated Pytest prepends the wheel's source directory to
`PYTHONPATH` so `from mypkg import ...` resolves against the source tree
without needing a `pip install` step.

## Running

```bash
devops test                # all tests
devops test MyLibTests     # just one
devops test --profile Release    # exercise tests against the release profile
```

`devops test` builds each selected test target first (so it's always
current), runs its binary / pytest invocation, and aggregates pass/fail.
Non-zero exit on any failure.
