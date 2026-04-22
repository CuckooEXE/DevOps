from builder import (
    COMMON_C_FLAGS,
    ElfBinary,
    ElfSharedObject,
    GoogleTest,
    Script,
    SphinxDocs,
    glob,
)

myLib = ElfSharedObject(
    name="MyCoolLib",
    srcs=glob("src/*.c"),
    includes=["include"],
)

myCoolApp = ElfBinary(
    name="MyCoolApp",
    srcs=glob(["main.c", "src/*.c"], exclude=["src/lib.c"]),
    includes=["include"],
    flags=COMMON_C_FLAGS,
    defs={"FOO": None, "BAR": "baz"},
    undefs=["QUX"],
    libs=[myLib],
)

pushAndRun = Script(
    name="pushAndRun",
    deps={"app": myCoolApp},
    cmds=[
        "echo scp {app.output_path} root@10.10.10.10:/tmp/",
        "echo ssh root@10.10.10.10 /tmp/{app.name}",
    ],
)

PushToTestInstance = Script(
    name="PushToTestInstance",
    script="scripts/PushToTestInstance.sh",
)

docs = SphinxDocs(
    name="docs",
    srcs=glob("docs/*"),
    conf="docs",
)

GoogleTest(
    name="MyCoolLibTests",
    srcs=glob("tests/*.cc"),
    target=myLib,
)
