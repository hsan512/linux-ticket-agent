"""Build data/processed/command_packages.json: which Ubuntu package provides each command.

Source: Ubuntu archive Contents index (see data/SOURCES.md).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import PROCESSED_DIR  # noqa: E402
from agent.tools.packages import build_command_packages  # noqa: E402

RAW_DIR = Path(__file__).parent / "raw" / "ubuntu"
RELEASES = {"noble": RAW_DIR / "Contents-noble-amd64.gz"}  # release codename -> Contents file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=PROCESSED_DIR / "command_packages.json")
    args = parser.parse_args()

    # {release codename: {command: [packages]}}
    command_packages = {codename: build_command_packages(path) for codename, path in RELEASES.items()}

    # sort_keys + indent: deterministic, diff-friendly output
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(command_packages, f, sort_keys=True, indent=2)

    for codename, commands in command_packages.items():
        print(f"{codename}: {len(commands)} commands")


if __name__ == "__main__":
    main()
