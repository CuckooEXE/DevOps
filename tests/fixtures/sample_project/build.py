"""Sample workspace project. Exercises every MVP target type + the
sugar/subclassing patterns the docs promise."""

from pathlib import Path

from builder import (
    COMMON_C_FLAGS,
    CObjectFile,
    CustomArtifact,
    DirectoryRef,
    ElfBinary,
    ElfSharedObject,
    GoogleTest,
    HeadersOnly,
    Install,
    LdBinary,
    PythonApp,
    PythonShiv,
    PythonWheel,
    Script,
    SphinxDocs,
    StaticLibrary,
    TestRangeTest,
    ZigBinary,
    ZigTest,
    glob,
)


# ---------------------------------------------------------------------------
# Shared header bundle — other targets pick this up as an include path.
# ---------------------------------------------------------------------------

headers = HeadersOnly(
    name="SampleHeaders",
    srcs=glob("include/*.h"),
    strip_prefix="include",
    doc="Public header bundle shared across library + binary targets.",
)


# ---------------------------------------------------------------------------
# Static library — exercises StaticLibrary + inheritance in GoogleTest.
# ---------------------------------------------------------------------------

mathStatic = StaticLibrary(
    name="MathStatic",
    srcs=glob("math_static/*.c"),
    includes=["include"],
    flags=COMMON_C_FLAGS,
    doc="Static math helpers (square/cube). Linked into MyCoolApp.",
)


# ---------------------------------------------------------------------------
# Shared library with an attached GoogleTest (explicit form).
# ---------------------------------------------------------------------------

myLib = ElfSharedObject(
    name="MyCoolLib",
    srcs=glob("src/*.c"),
    includes=["include"],
    doc="Shared library exposing add() and greeting(). Used by MyCoolApp.",
)

GoogleTest(
    name="MyCoolLibTests",
    srcs=glob("tests/test_mylib.cc"),
    target=myLib,
    doc="GoogleTest-driven unit tests for MyCoolLib. Links libMyCoolLib.so.",
)


# ---------------------------------------------------------------------------
# Subclassed target type — shows how a team pins flags once.
# ---------------------------------------------------------------------------

class TeamBinary(ElfBinary):
    """ElfBinary with the team's mandatory warning flags baked in.

    Equivalent to writing ``flags=COMMON_C_FLAGS + ('-Werror', ...)`` on
    every binary, but less to forget.
    """

    def __init__(self, **kwargs: object) -> None:
        merged_flags = tuple(COMMON_C_FLAGS) + ("-Werror",)
        user_flags = tuple(kwargs.pop("flags", ()) or ())  # type: ignore[arg-type]
        super().__init__(flags=merged_flags + user_flags, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The user-facing binary. Uses the subclass, links both libs, and uses
# `tests=` sugar to register a sibling GoogleTest in one go.
# ---------------------------------------------------------------------------

myCoolApp = TeamBinary(
    name="MyCoolApp",
    srcs=glob(["main.c", "src/*.c"], exclude=["src/lib.c"]),
    # `headers` is a HeadersOnly target — the binary picks up -I<staged dir>
    # automatically, and `headers` flows into deps for topo-sort.
    includes=[headers, "vendor/greet_remote/include"],
    defs={"FOO": None, "BAR": "baz"},
    undefs=["QUX"],
    libs=[
        myLib,
        mathStatic,
        # Typed remote reference. DirectoryRef is hermetic (no network);
        # swap for GitRef("ssh://...", target="greetRemote", ref="v1.2.3")
        # or TarballRef("https://.../pkg.tar.gz", target=...) in real use.
        # Absolute path here so resolution isn't cwd-sensitive at link time.
        DirectoryRef(
            str(Path(__file__).parent / "vendor" / "greet_remote"),
            target="greetRemote",
        ),
    ],
    tests={"srcs": glob("tests/test_math_static.cc")},
    doc="""User-facing binary. Subclasses ElfBinary via TeamBinary so the
        team's `-Werror` policy is enforced; also demonstrates the
        `tests=` sugar creating a MyCoolAppTests GoogleTest automatically.""",
)


# ---------------------------------------------------------------------------
# Python wheel + its pytest suite (via tests= sugar).
# ---------------------------------------------------------------------------

mypkg = PythonWheel(
    name="mypkg",
    pyproject="pytools/pyproject.toml",
    srcs=glob("pytools/mypkg/**/*.py"),
    tests={"srcs": glob("pytools/tests/test_*.py")},
    doc="Sample Python package. Builds a wheel and runs pytest via tests= sugar.",
)


# ---------------------------------------------------------------------------
# Scripts — both inline-cmds (with multi-key deps templating) and a file.
# ---------------------------------------------------------------------------

pushAndRun = Script(
    name="pushAndRun",
    deps={"app": myCoolApp, "lib": myLib},
    cmds=[
        "echo scp {app.output_path} {lib.output_path} root@10.10.10.10:/tmp/",
        "echo ssh root@10.10.10.10 LD_LIBRARY_PATH=/tmp /tmp/{app.name}",
    ],
    doc="Stages MyCoolApp + libMyCoolLib.so on the test box and runs the app.",
)

Script(
    name="PushToTestInstance",
    script="scripts/PushToTestInstance.sh",
    doc="Thin wrapper that shells out to scripts/PushToTestInstance.sh.",
)


# ---------------------------------------------------------------------------
# Sphinx docs.
# ---------------------------------------------------------------------------

SphinxDocs(
    name="sampledocs",
    srcs=glob("docs/*"),
    conf="docs",
    doc="HTML rendering of the sample project's own docs/ directory.",
)


# ---------------------------------------------------------------------------
# Zig target — delegates to `zig build` against zigapp/build.zig.
# ---------------------------------------------------------------------------

zigapp = ZigBinary(
    name="zigapp",
    project_dir="zigapp",
    doc="Zig-built binary produced by `zig build` against zigapp/build.zig.",
)

ZigTest(
    name="zigappTests",
    project_dir="zigapp",
    doc="Runs `zig build test` against zigapp/build.zig.",
)


# ---------------------------------------------------------------------------
# CustomArtifact — arbitrary post-processing (here: strip MyCoolApp).
# ---------------------------------------------------------------------------

CustomArtifact(
    name="MyCoolAppStripped",
    inputs={"bin": myCoolApp},
    outputs=["MyCoolApp.stripped"],
    cmds=[
        "cp {bin.output_path} {out[0]}",
        "strip --strip-all {out[0]}",
    ],
    doc="Copies MyCoolApp and runs `strip --strip-all` — demo of CustomArtifact.",
)


# ---------------------------------------------------------------------------
# PythonApp — runnable with auto-venv, honours requirements.txt.
# ---------------------------------------------------------------------------

PythonApp(
    name="mypkg-app",
    entry="mypkg.cli:main",
    pyproject="pytools/pyproject.toml",
    requirements="pytools/requirements.txt",
    doc="Dev-friendly runner for mypkg.cli:main with auto-managed venv.",
)

# Same CLI, but delivered as a single-file .pyz.
PythonShiv(
    name="mypkg-shiv",
    entry="mypkg.cli:main",
    pyproject="pytools/pyproject.toml",
    requirements="pytools/requirements.txt",
    doc="Distributable single-file .pyz bundling mypkg + its deps.",
)


# ---------------------------------------------------------------------------
# Install targets — stage built artifacts outside the build tree.
# Use a fixture-local /tmp path so `devops install` works without root.
# ---------------------------------------------------------------------------

Install(
    name="install-app",
    artifact=myCoolApp,
    dest="/tmp/sample_project_install/bin",
    mode="0755",
    doc="Stage MyCoolApp under /tmp/sample_project_install/bin (no sudo).",
)

Install(
    name="install-lib",
    artifact=myLib,
    dest="/tmp/sample_project_install/lib",
    mode="0644",
    doc="Stage libMyCoolLib.so for runtime discovery by the installed binary.",
)

Install(
    name="install-headers",
    artifact=headers,
    dest="/tmp/sample_project_install/include",
    doc="Copy the public header bundle under /tmp/sample_project_install/include.",
)

Install(
    name="install-pkg",
    artifact=mypkg,
    pip_args=("--user", "--break-system-packages", "--force-reinstall"),
    doc="pip-install the mypkg wheel into the user site (dogfooding).",
)


# ---------------------------------------------------------------------------
# CObjectFile + LdBinary — classic compile / link split. Here we produce a
# relocatable object (`ld -r`) that combines two translation units into
# one .o, which is the most portable ld demo (no libc startup needed).
# ---------------------------------------------------------------------------

relocParts = CObjectFile(
    name="relocParts",
    srcs=glob("reloc/*.c"),
    doc="Compiles reloc/part_a.c and reloc/part_b.c to separate .o files.",
)

LdBinary(
    name="relocCombined",
    objs=[relocParts],
    extra_ld_flags=("-r",),  # produce a relocatable (partial-link) output
    doc="Merges relocParts .o files into one relocatable object via `ld -r`.",
)


# ---------------------------------------------------------------------------
# CustomArtifact with required_tools= so `devops doctor` can preflight it.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# TestRangeTest — libvirt-backed e2e smoke test. Reads MyCoolApp's host
# path from DEVOPS_ARTIFACT_APP and uploads it into the VM. Requires a
# globally-installed `testrange` + libvirt; skipped on machines without.
# ---------------------------------------------------------------------------

TestRangeTest(
    name="MyCoolAppE2E",
    srcs=glob("tests/e2e/*.py"),
    artifacts={"app": myCoolApp, "mylib": myLib},
    doc="End-to-end: boot a Debian VM, upload MyCoolApp + libMyCoolLib.so.",
)


CustomArtifact(
    name="MyCoolAppSize",
    inputs={"bin": myCoolApp},
    outputs=["MyCoolApp.size.txt"],
    cmds=["size {bin.output_path} > {out[0]}"],
    required_tools=["size"],
    doc="Runs `size` on MyCoolApp and captures the per-section byte counts.",
)
