"""Stand-in for an external project. Pulled in via DirectoryRef from the
top-level sample build.py to exercise remote-ref resolution offline."""

from builder import ElfSharedObject, glob

ElfSharedObject(
    name="greetRemote",
    srcs=glob("src/*.c"),
    includes=["include"],
    doc="Shared library consumed by MyCoolApp via a DirectoryRef.",
)
