"""Download the Unix & Linux Stack Exchange dump and extract the XML files we need.

Source: community release of the Stack Exchange data dump on the Internet Archive.
See data/SOURCES.md for license and attribution details.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

import py7zr

DUMP_ID = "stackexchange_20251231"
ARCHIVE_NAME = "unix.stackexchange.com.7z"
URL = f"https://archive.org/download/{DUMP_ID}/{DUMP_ID}/{ARCHIVE_NAME}"
# From https://archive.org/metadata/stackexchange_20251231
EXPECTED_SIZE = 753_598_550
EXPECTED_MD5 = "2b0958a9d28dacf0484d98d88addd7cb"
TARGETS = ["Posts.xml", "Users.xml"]

RAW_DIR = Path(__file__).parent / "raw"


def md5sum(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def is_valid(path: Path) -> bool:
    return (
        path.exists()
        and path.stat().st_size == EXPECTED_SIZE
        and md5sum(path) == EXPECTED_MD5
    )


def download(url: str, dest: Path, chunk_size: int = 1 << 20) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as resp, tmp.open("wb") as out:
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        while chunk := resp.read(chunk_size):
            out.write(chunk)
            done += len(chunk)
            if total:
                print(
                    f"\r{done / 1e6:8.1f} / {total / 1e6:.1f} MB",
                    end="",
                    file=sys.stderr,
                )
    print(file=sys.stderr)
    tmp.rename(dest)


def extract(archive: Path, out_dir: Path, targets: list[str]) -> None:
    with py7zr.SevenZipFile(archive, mode="r") as z:
        z.extract(path=out_dir, targets=targets)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    args = parser.parse_args()

    args.raw_dir.mkdir(parents=True, exist_ok=True)
    archive = args.raw_dir / ARCHIVE_NAME

    if is_valid(archive):
        print(f"{archive} already present and verified")
    else:
        print(f"Downloading {URL}")
        download(URL, archive)
        if not is_valid(archive):
            sys.exit(f"Checksum/size mismatch for {archive}; delete it and retry")
        print("Checksum OK")

    missing = [t for t in TARGETS if not (args.raw_dir / t).exists()]
    if missing:
        print(f"Extracting {missing}")
        extract(archive, args.raw_dir, missing)
    print("Done")


if __name__ == "__main__":
    main()
