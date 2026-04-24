# devops graph

Export the dependency DAG in one of three formats. Useful for:

- Visualizing a sprawling workspace (`devops graph | dot -Tsvg > g.svg`).
- Feeding IDE integrations or tooling (`--format=json`).
- Spot-checking a single target's ancestry (`devops graph MyApp --format=text`).

## Formats

| `--format=` | For                                                 |
|-------------|-----------------------------------------------------|
| `dot`       | Graphviz (pipe to `dot -Tsvg`, `dot -Tpng`).         |
| `json`      | Machine-readable. Nodes + edges + profile metadata.  |
| `text`      | Indented tree for quick terminal skim.               |

Default is `dot`.

## Scoping

- `devops graph` — every registered target in the workspace.
- `devops graph MyApp OtherApp` — forward-transitive subgraph rooted at
  the named targets. Deps and deps-of-deps are included; unrelated
  targets are not.

## Edge kinds

JSON edges carry a `kind` field derived from how the dep was declared:

| kind       | source                                                        |
|------------|---------------------------------------------------------------|
| `dep`      | plain `deps={...}` kwarg                                      |
| `lib`      | `libs=[...]` on an ElfBinary / SharedObject                   |
| `include`  | `includes=[...]` on an ElfBinary / SharedObject / HeadersOnly |
| `obj`      | `objs=[...]` on an LdBinary                                   |
| `input`    | `inputs={...}` on a CustomArtifact                            |
| `install`  | the Artifact an Install target targets                        |

`dot` emits edges without labels — the kind is present in JSON for
tooling that wants it, but dot stays readable by default.

## Remote refs

`GitRef` / `TarballRef` / `DirectoryRef` entries in `libs=` / `includes=`
are **opaque** by default — one synthetic `RemoteRef` node per ref,
labeled by its spec string, no network fetch. Pass `--resolve-remotes`
to clone/fetch each ref and inline the resolved target's subgraph.

## Output destination

- Stdout by default (pipe-friendly).
- `--output <path>` writes to a file; `-o -` is an explicit stdout.

## Cycles

`devops graph` never crashes on a cyclic graph — the cycle is emitted
as a comment in `dot`, as a `cycles: [...]` block in `json`, and as a
`cycles:` section at the end of `text`. Use the command to diagnose a
cycle that `devops build` is refusing to start.

## Examples

```sh
# full workspace as an SVG
devops graph | dot -Tsvg > graph.svg

# just one target's subgraph
devops graph MyCoolApp --format=text

# JSON for jq / other tools
devops graph --format=json | jq '.edges[] | select(.kind == "lib")'

# resolve remote refs (triggers network)
devops graph --resolve-remotes --format=text
```
