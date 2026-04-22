# Installing artifacts

The `Install` target stages a built artifact outside the `build/` tree:
under `/usr/local/bin`, into your Python site-packages, into a tarball
drop zone, etc.

## Binary / library install

```python
myApp = ElfBinary(name="myApp", srcs=glob("main.c"))

Install(
    name="install-myApp",
    artifact=myApp,
    dest="/usr/local/bin",
    mode="0755",          # default
    sudo=True,            # prefix `sudo` if dest isn't user-writable
)

Install(
    name="install-myLib",
    artifact=ElfSharedObject(name="myLib", srcs=glob("src/*.c")),
    dest="/usr/local/lib",
    mode="0644",
    sudo=True,
)
```

Under the hood: `install -m <mode> -D <src> <dest>/<filename>`. The
`-D` flag creates intermediate directories as needed.

`HeadersOnly` is copied recursively:

```python
headers = HeadersOnly(name="myh", srcs=glob("include/**/*.h"))
Install(name="install-myh", artifact=headers, dest="/usr/local/include")
```

## Python wheel install

For a `PythonWheel`, `dest=` is ignored; the wheel is fed to `pip
install`:

```python
pkg = PythonWheel(name="mypkg", pyproject="pyproject.toml")

Install(
    name="install-pkg",
    artifact=pkg,
    pip_args=("--user",),             # default
    # or: pip_args=("--break-system-packages", "--user"),
    # or: pip_args=() to use the active venv
)
```

## Running

```bash
devops install                      # every Install target in the workspace
devops install install-myApp        # just one
devops install install-pkg install-myLib
```

The Install target topologically builds its `artifact=` first, so
`devops install` Just Works — no need to remember to `devops build`
beforehand.
