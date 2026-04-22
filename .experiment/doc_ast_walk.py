"""AST-walk reference implementation for docstring-style target docs.

================================================================================
Purpose
================================================================================

This file is a *reference* implementation of a feature we considered but did
not land: letting build.py authors document a target by placing a bare string
literal on the line after the target's constructor, like this:

    myCoolApp = ElfBinary(
        name="MyCoolApp",
        srcs=glob("main.c"),
    )
    \"\"\"MyCoolApp is the user-facing binary. It links against MyCoolLib.\"\"\"

Python does NOT attach that string to anything on its own — it's a no-op
expression statement, not a docstring. Python's native docstring system only
recognises the first string literal in a module, class, or function body.

So to get the syntax above to actually do something, we need to parse the
build.py source with `ast`, find string literals that follow Target
assignments/calls, and attach them to the registered Target instances after
the module has been exec()'d.

The explicit `doc="..."` kwarg (the approach we shipped instead) is simpler
and has no magic. But if later we decide the post-constructor string literal
is worth the AST parsing cost, this file is the drop-in.

================================================================================
How to integrate
================================================================================

1. Copy `extract_docs()` and `attach_docs_to_targets()` from this file into
   `devops/workspace.py` (or a new `devops/docs_parser.py` module).

2. In `devops/workspace.py`, replace the body of `_load_build_py` with:

       def _load_build_py(path: Path, project: Project) -> None:
           source = path.read_text()
           doc_map = extract_docs(source)  # <-- NEW
           module_name = f"devops._build_{project.name}_{abs(hash(str(path)))}"
           spec = importlib.util.spec_from_file_location(module_name, path)
           module = importlib.util.module_from_spec(spec)
           sys.modules[module_name] = module
           with registry.active_project(project):
               before = set(registry.all_targets())
               spec.loader.exec_module(module)
               new_targets = [t for t in registry.all_targets() if t not in before]
               attach_docs_to_targets(module, new_targets, doc_map)  # <-- NEW

3. `Target` already has a `doc` attribute from the `doc=` kwarg ship.
   `attach_docs_to_targets` writes to that same attribute; the AST walk loses
   to an explicit `doc="..."` kwarg when both are present (by design — the
   kwarg is the source of truth).

4. Remove or deprecate the `doc=` kwarg if you want the AST-walk syntax to
   be the only way. Or keep both; they compose fine.

5. Tests to add:
   - tests/test_doc_ast_walk.py with cases for:
     * assignment form:     myLib = ElfBinary(...)   followed by \"\"\"...\"\"\"
     * bare-call form:       Script(name=\"foo\", ...)  followed by \"\"\"...\"\"\"
     * multiple targets in one module
     * strings NOT following a target (ignored)
     * multiline triple-quoted strings (preserved verbatim after dedent)
     * targets with no following string (doc stays unset / empty)

================================================================================
Limitations
================================================================================

* Bare-call targets (no assignment, just `Script(name=..., ...)` at module
  scope) are matched by the `name=` kwarg, which must be a string literal.
  If `name` is computed, the match is skipped.

* If a user reassigns a variable (`myLib = ...; myLib = ElfBinary(...)` then
  a doc string), the doc attaches to the final binding's target. That's
  correct for this DSL but worth noting.

* Conditional target construction (`if COND: x = ElfBinary(...)`) inside
  build.py: we only walk module-level `body`. Targets registered inside `if`
  bodies or loops won't get docs from this walk. That's acceptable because
  placing a doc string after a conditionally-created target is itself weird.

* Performance: ast.parse + a linear walk is microseconds on realistic
  build.py sizes. Not a concern.
"""

from __future__ import annotations

import ast
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from devops.core.target import Target


# A (binding_kind, binding_key, doc) where:
#   binding_kind="var"        binding_key is the assigned variable name
#   binding_kind="name_kwarg" binding_key is the string value of the name=... kwarg
DocRecord = tuple[str, str, str]


def extract_docs(source: str) -> list[DocRecord]:
    """Parse a build.py source string, return doc records in source order.

    Finds every pair (stmt, next_stmt) where:
      * stmt is `Assign(targets=[Name(id=X)], value=Call(...))`
        or       `Expr(value=Call(..., keywords=[..., name="X", ...]))`
      * next_stmt is `Expr(value=Constant(value=<str>))`

    Everything else is ignored.
    """
    tree = ast.parse(source)
    out: list[DocRecord] = []
    body = tree.body
    for i in range(len(body) - 1):
        nxt = body[i + 1]
        if not (
            isinstance(nxt, ast.Expr)
            and isinstance(nxt.value, ast.Constant)
            and isinstance(nxt.value.value, str)
        ):
            continue
        doc = nxt.value.value

        node = body[i]
        # Form 1: myLib = ElfSharedObject(...)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    out.append(("var", tgt.id, doc))
            continue

        # Form 2: Script(name="foo", ...)  — bare call at module scope
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            for kw in node.value.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    out.append(("name_kwarg", kw.value.value, doc))
                    break
    return out


def attach_docs_to_targets(
    module: ModuleType,
    new_targets: list["Target"],
    records: list[DocRecord],
) -> None:
    """Apply extracted docs to the Target instances registered by this module.

    Precedence:
        explicit doc=... kwarg  >  AST-walk attached doc  (existing doc wins)

    That way, users can override the AST-inferred doc with an explicit kwarg.
    """
    if not records:
        return

    # Build var-name → Target lookup: scan the module namespace for variables
    # that point at Target instances we just registered.
    target_set = set(id(t) for t in new_targets)
    var_to_target: dict[str, "Target"] = {}
    for var, value in module.__dict__.items():
        if id(value) in target_set:
            var_to_target[var] = value

    # name-kwarg → Target lookup: match by target.name
    name_to_target: dict[str, "Target"] = {}
    for t in new_targets:
        name_to_target.setdefault(t.name, t)

    for kind, key, doc in records:
        target: "Target | None"
        if kind == "var":
            target = var_to_target.get(key)
        elif kind == "name_kwarg":
            target = name_to_target.get(key)
        else:
            target = None
        if target is None:
            continue
        # Respect an explicit doc=... kwarg (don't overwrite)
        if not getattr(target, "doc", None):
            target.doc = doc.strip()


# -------- self-test (run this file directly) --------

if __name__ == "__main__":
    sample = '''
from builder import ElfBinary, ElfSharedObject, Script, glob

myLib = ElfSharedObject(name="MyCoolLib", srcs=glob("src/*.c"))
"""The cool library."""

myCoolApp = ElfBinary(name="MyCoolApp", srcs=glob("main.c"), libs=[myLib])
"""User-facing binary. Depends on MyCoolLib."""

Script(name="pushAndRun", cmds=["scp x host:/"])
"""Pushes to the test box."""

noDoc = ElfBinary(name="NoDoc", srcs=glob("main.c"))

x = 42
"""Not attached to anything."""
'''
    for kind, key, doc in extract_docs(sample):
        print(f"{kind:>10} {key:<15} → {doc!r}")
