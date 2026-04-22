# Scripts

A `Script` target runs a series of commands but produces no tracked
output. Use scripts for deployment, one-shot maintenance jobs, or
wrapping existing shell scripts.

## Two forms: `cmds=` and `script=`

A Script must declare **exactly one** of:

```python
Script(name="inline", cmds=["echo one", "echo two"])
Script(name="from_file", script="scripts/do_thing.sh")
```

The `cmds=` list runs each entry under `sh -c`. `script=` runs a single
file under `bash`.

## Templating in `cmds=`

Keys of the `deps=` dict become template variables interpolated at run time:

```python
Script(
    name="deploy",
    deps={"app": myApp, "lib": myLib},
    cmds=[
        "scp {app.output_path} {lib.output_path} host:/opt/",
        "ssh host 'systemctl restart {app.name}'",
    ],
)
```

Available attributes on each dep:

| Attribute      | Value                                             |
| -------------- | ------------------------------------------------- |
| `name`         | The target's short name                           |
| `qualified_name` | `<project>::<name>`                              |
| `project`      | The dep's project name                            |
| `output_path`  | Artifact's built path (empty string for Scripts)  |
| `output_dir`   | Artifact's output directory                       |
| `version`      | Git-describe / VERSION / explicit override        |

Using `{app}` without a dotted attribute defaults to `output_path` for
Artifacts, or `name` for Scripts.

Unknown attributes raise `AttributeError` at run time, so typos surface
immediately.

## Dependency ordering

`Script` deps are treated as build-time dependencies: `devops run
<script>` topologically builds every `Artifact` dep first, then executes
the script's cmds. A Script that depends on another Script runs it
beforehand.

## When to prefer a Script vs a subcommand

- `devops run <script>` is a good fit for **deployment / one-shot**
  operations tied to particular artifacts.
- If it looks like a test, use `GoogleTest` or `Pytest` so it plugs into
  `devops test`.
- If it looks like linting, fold it into a target's `lint_cmds()` (see
  {doc}`../developer_guide/adding_a_tool`).
