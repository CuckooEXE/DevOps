"""Sample workspace project. Exercises every MVP target type + the
sugar/subclassing patterns the docs promise."""

from builder import (
    COMMON_C_FLAGS,
    ElfBinary,
    ElfSharedObject,
    GoogleTest,
    HeadersOnly,
    PythonWheel,
    Script,
    SphinxDocs,
    StaticLibrary,
    glob,
)


# ---------------------------------------------------------------------------
# Shared header bundle — other targets pick this up as an include path.
# ---------------------------------------------------------------------------

headers = HeadersOnly(
    name="SampleHeaders",
    srcs=glob("include/*.h"),
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
    includes=["include"],
    defs={"FOO": None, "BAR": "baz"},
    undefs=["QUX"],
    libs=[myLib, mathStatic],
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
