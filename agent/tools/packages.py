from __future__ import annotations

import gzip
import json
import os
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from agent.schemas import Evidence

BIN_DIRS = frozenset({"usr/bin", "usr/sbin", "bin", "sbin"})
RELEASE_NAMES = {"noble": "Ubuntu 24.04", "jammy": "Ubuntu 22.04"}  # newest first
PACKAGE_URL = "https://packages.ubuntu.com/{release}/{package}"
CONTENTS_SEARCH_URL = ("https://packages.ubuntu.com/search?searchon=contents&keywords={command}"
                       "&mode=exactfilename&suite={release}&arch=any")


def parse_contents_line(line: str) -> tuple[str, list[str]] | None:
    """Parse one line of an Ubuntu Contents index into (command, packages).

    A line has two whitespace-separated columns: a file path, and a comma-separated
    list of "[component/]section/package" entries that ship that file.

    Returns None if the line does not have exactly two columns, if the file is not
    directly inside usr/bin, usr/sbin, bin, or sbin, or if it is a hidden file
    (name starts with "."); in those cases it is not a command.

    >>> parse_contents_line("usr/bin/VBoxClient\\t\\tmultiverse/x11/a,multiverse/x11/b\\n")
    ('VBoxClient', ['a', 'b'])
    """
    parts = line.split()
    if len(parts) != 2:
        return None
    path, packages_column = parts
    if os.path.dirname(path) not in BIN_DIRS:
        return None
    command = os.path.basename(path)
    if command.startswith("."):
        return None  # hidden files are not commands
    names = [entry.rpartition("/")[2] for entry in packages_column.split(",")]
    packages = [name for name in names if name]
    return command, packages


def build_command_packages(contents_path: Path) -> dict[str, list[str]]:
    """Parse lines of an Ubuntu Contents index into a command -> packages mapping.

    Lines for which parse_contents_line returns None (malformed, or not a command)
    are ignored. A command listed on several lines gets the union of their packages.
    """
    mapping: dict[str, set[str]] = defaultdict(set)
    with gzip.open(contents_path, "rt", encoding="utf-8") as f:
        for line in f:
            parsed = parse_contents_line(line)
            if parsed is not None:
                command, packages = parsed
                mapping[command].update(packages)
    # Convert sets to sorted lists for consistent output
    return {command: sorted(packages) for command, packages in mapping.items()}


def load_command_packages(path: Path) -> dict[str, dict[str, list[str]]]:
    """Load a release -> command -> packages mapping written by data/build_packages.py."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def lookup_command_packages(
    command: str, command_packages: dict[str, dict[str, list[str]]]
) -> dict[str, list[str]]:
    """Look up which packages provide a command, per release.

    If the exact name is not found in a release, falls back to names of the form
    "command.<variant>" (Debian alternatives, e.g. nc -> nc.openbsd, nc.traditional).
    Releases where nothing matches are left out; returns {} if no release matches.
    The input mapping is never modified.
    """
    result: dict[str, list[str]] = {}
    for release, commands in command_packages.items():
        found = set()
        packages = commands.get(command, [])
        if len(packages) > 0:
            found.update(packages)
        else:
            for cmd, pkg in commands.items():
                if cmd.startswith(command + "."):
                    found.update(pkg)
        if len(found) > 0:
            result[release] = sorted(found)
    return result


def release_label(codename: str) -> str:
    """'noble' -> 'Ubuntu 24.04 (noble)'; unknown codenames are shown as they are."""
    name = RELEASE_NAMES.get(codename)
    return f"{name} ({codename})" if name else codename


class CommandPackageLookup:
    """The check_command tool: holds the release -> command -> packages table and answers
    one command per call as a single Evidence passage (found or not found)."""

    def __init__(self, command_packages: dict[str, dict[str, list[str]]]) -> None:
        if not command_packages:
            raise ValueError("command_packages is empty: run data/build_packages.py")
        self._command_packages = command_packages
        # newest first (RELEASE_NAMES order), then unknown codenames alphabetically
        known = [r for r in RELEASE_NAMES if r in command_packages]
        self._releases = known + sorted(set(command_packages) - set(RELEASE_NAMES))

    def lookup_command(self, command: str) -> list[Evidence]:
        """Look up one command; always returns exactly one Evidence (id is renumbered later)."""
        found = lookup_command_packages(command, self._command_packages)
        present = [r for r in self._releases if r in found]
        missing = [r for r in self._releases if r not in found]
        if present:
            lines = [f"{command} is provided by these packages (install any one):"]
            lines += [f"- {release_label(r)}: {', '.join(found[r])}" for r in present]
            if missing:
                lines.append(f"Not found in: {', '.join(release_label(r) for r in missing)}.")
            url = PACKAGE_URL.format(release=present[0], package=found[present[0]][0])
        else:
            lines = [f"{command}: not found in the {', '.join(release_label(r) for r in missing)} "
                     "package index. This does not prove it does not exist: it may be a shell builtin, "
                     "a name set up at install time, or software from outside Ubuntu's archive."]
            url = CONTENTS_SEARCH_URL.format(command=quote(command, safe=""), release=missing[0])
        return [Evidence(id="P1", source="package", title=f"{command} (Ubuntu package index)", url=url,
                         text="\n".join(lines),
                         score=1.0 if present else 0.0)]  # not a similarity: 1.0 found, 0.0 not found
