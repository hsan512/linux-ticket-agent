import copy
import gzip
import json
from pathlib import Path

import pytest

from agent.tools.packages import (
    build_command_packages,
    load_command_packages,
    lookup_command_packages,
    parse_contents_line,
)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # --- parsed (column 2 split on commas; package = text after the last "/") ---
        ("usr/bin/rsync\t\t\tnet/rsync\n", ("rsync", ["rsync"])),                     # section/package
        ("usr/bin/fzf\t\t\tuniverse/utils/fzf\n", ("fzf", ["fzf"])),                  # component/section/package
        ("usr/bin/OgreMeshUpgrader\t\tuniverse/devel/ogre-1.12-tools,universe/devel/ogre-1.9-tools\n",
         ("OgreMeshUpgrader", ["ogre-1.12-tools", "ogre-1.9-tools"])),                 # several packages
        ("usr/sbin/sshd\t\t\tnet/openssh-server\n", ("sshd", ["openssh-server"])),     # usr/sbin
        ("bin/ls\t\t\tutils/coreutils\n", ("ls", ["coreutils"])),                      # bin
        ("sbin/fsck\t\t\tadmin/util-linux\n", ("fsck", ["util-linux"])),               # sbin
        ("usr/bin/mkfs.ext4\t\tadmin/e2fsprogs\n", ("mkfs.ext4", ["e2fsprogs"])),       # dot in name is kept
        ("usr/bin/tool\t\tnet/pkg-a,,net/pkg-b\n", ("tool", ["pkg-a", "pkg-b"])),       # empty entry dropped
        # --- None: not directly in a bin directory, so not a command ---
        ("usr/share/doc/rsync/README\t\tnet/rsync\n", None),                           # documentation file
        ("usr/lib/foo/bin/inner\t\tlibs/foo\n", None),                                 # "bin" nested deeper
        ("usr/bin/X11/xterm\t\tx11/xterm\n", None),                                    # subdirectory of usr/bin
        ("binaries/foo\t\tutils/foo\n", None),                                         # starts with "bin" but is not bin/
        ("usr/bin/foo/\t\tnet/x\n", None),                                             # trailing "/" in path is not a command
        ("usr/bin/.kcapi-hasher.hmac\t\t\tuniverse/utils/kcapi-tools\n", None),        # hidden file, not a command
        # --- None: malformed line (not exactly two columns) ---
        ("", None),                                                                     # empty string
        ("   \n", None),                                                                # whitespace only
        ("usr/bin/foo\n", None),                                                        # missing column 2
        ("usr/bin/my tool\t\tutils/x\n", None),                                         # three columns (space in file name)
    ],
)
def test_parse_contents_line(line: str, expected: tuple[str, list[str]] | None) -> None:
    assert parse_contents_line(line) == expected


def write_contents(path: Path, lines: list[str]) -> Path:
    """Write a small fake Contents index (gzip-compressed, like the real one)."""
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.writelines(lines)
    return path


def test_build_command_packages(tmp_path: Path) -> None:
    contents = write_contents(tmp_path / "Contents-amd64.gz", [
        "bin/ls\t\t\tutils/coreutils\n",                       # same command and package on two lines
        "usr/bin/ls\t\t\tutils/coreutils\n",
        "bin/nc\t\t\tnet/netcat-traditional\n",                # same command, different packages:
        "usr/bin/nc\t\t\tnet/netcat-openbsd\n",                # merged, then sorted (openbsd < traditional)
        "usr/bin/parallel\t\tuniverse/utils/moreutils,universe/utils/parallel\n",  # two packages on one line
        "usr/share/doc/coreutils/README\t\tutils/coreutils\n",   # not a command: ignored
        "usr/bin/foo\n",                                         # malformed (one column): ignored
    ])
    assert build_command_packages(contents) == {
        "ls": ["coreutils"],
        "nc": ["netcat-openbsd", "netcat-traditional"],
        "parallel": ["moreutils", "parallel"],
    }


def test_build_command_packages_empty_file(tmp_path: Path) -> None:
    contents = write_contents(tmp_path / "Contents-amd64.gz", [])
    assert build_command_packages(contents) == {}

# Two releases on purpose: bugs that leak state between releases only show up with more than one.
TABLE = {
    "noble": {
        "ls": ["coreutils"],
        "nc.openbsd": ["netcat-openbsd"],        # alternatives: no plain "nc" file in the index
        "nc.traditional": ["netcat-traditional"],
        "mkfs": ["util-linux"],                  # a real command that also has "mkfs.*" relatives
        "mkfs.ext4": ["e2fsprogs"],
        "vim.basic": ["vim"],
        "virtualgps.py": ["virtualgps"],         # starts with "vi" and contains "." but is unrelated to vi
    },
    "jammy": {
        "ls": ["coreutils"],
        "fdfind": ["fd-find"],
    },
}


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("ls", {"noble": ["coreutils"], "jammy": ["coreutils"]}),           # exact match in both releases
        ("fdfind", {"jammy": ["fd-find"]}),                                   # exact match in one release only
        ("nc", {"noble": ["netcat-openbsd", "netcat-traditional"]}),          # fallback to nc.*; jammy has none
        ("mkfs", {"noble": ["util-linux"]}),                                  # exact match wins: no fallback to mkfs.*
        ("mkfs.ext4", {"noble": ["e2fsprogs"]}),                              # dotted names can be looked up directly
        ("vim", {"noble": ["vim"]}),                                          # fallback to vim.basic
        ("vi", {}),                                                           # "vi." prefix only: vim.basic, virtualgps.py don't match
        ("missing", {}),                                                      # not found anywhere
    ],
)
def test_lookup_command_packages(command: str, expected: dict[str, list[str]]) -> None:
    assert lookup_command_packages(command, TABLE) == expected


def test_lookup_command_packages_does_not_modify_table() -> None:
    table = copy.deepcopy(TABLE)
    first = lookup_command_packages("nc", table)
    second = lookup_command_packages("nc", table)
    assert first == second                    # repeated calls give the same answer
    assert table == TABLE                     # the shared table is untouched
    first["noble"].append("junk")             # mutating a result must not reach the table
    assert table == TABLE


def test_load_command_packages_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "command_packages.json"
    path.write_text(json.dumps(TABLE), encoding="utf-8")
    assert load_command_packages(path) == TABLE
