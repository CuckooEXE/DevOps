# Getting started

## Install

Editable install from the repo root:

```bash
pip install --user -e .
```

This drops the `devops` command into `~/.local/bin`. Make sure that's on
your `PATH`.

Check it works:

```bash
devops --help
```

Optionally, set up shell completion:

```bash
devops --install-completion zsh      # or bash, fish
```

After a shell reload, `devops run <TAB>` offers the names of runnable
targets in the current workspace.

## Your first `build.py`

Create a project directory with a source file and a `build.py`:

```
myproj/
├── main.c
└── build.py
```

`main.c`:

```c
#include <stdio.h>
int main(void) { puts("hello"); return 0; }
```

`build.py`:

```python
from builder import ElfBinary, glob

ElfBinary(
    name="hello",
    srcs=glob("main.c"),
    doc="Prints 'hello' and exits.",
)
```

Then, from inside `myproj/`:

```bash
devops describe
devops cmds hello          # print commands that would run, don't run them
devops build hello
devops run hello           # prints: hello
```

## Workspaces

`devops` walks up from the current directory looking for a `devops.toml`
file (or `.git` as a fallback) to treat as the **workspace root**. Every
directory under that root containing a `build.py` is a **project**.

A minimal `devops.toml` is just:

```toml
# devops.toml
```

Add `[toolchain]` entries if you need Docker-wrapped compilers — see
{doc}`docker_toolchains`.
