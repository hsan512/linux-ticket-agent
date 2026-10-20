"""Build the documentation corpus (man pages + GNU manuals) as JSONL, fully offline.

Man pages are chosen from command names and tags in the *history* split only (questions before
HISTORY_CUTOFF), so the benchmark never influences what is in the corpus.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import HISTORY_CUTOFF, PROCESSED_DIR  # noqa: E402
from agent.tools.command_check import collect_known_commands, command_names  # noqa: E402

MAN_SECTIONS = "1,8,5,7"
MAN_SELECTION = Path(__file__).parent / "man_pages.json"  # frozen list of man doc_ids in the corpus
MIN_DOC_FREQ = 5  # a name must appear in at least this many history posts
SYSTEM_INFO_DIR = Path("/usr/share/info")
# bash-doc and gawk-doc .debs fetched with `apt-get download` and unpacked with `dpkg-deb -x`
EXTRA_INFO_DIR = Path(__file__).parent / "raw" / "gnu" / "extracted" / "usr" / "share" / "info"
# info file stem -> (directory, GNU project slug for URLs, Debian package for licensing)
INFO_MANUALS = {
    "bash": (EXTRA_INFO_DIR, "bash", "bash-doc"),
    "gawk": (EXTRA_INFO_DIR, "gawk", "gawk-doc"),
    "coreutils": (SYSTEM_INFO_DIR, "coreutils", "coreutils"),
    "find": (SYSTEM_INFO_DIR, "findutils", "findutils"),
    "grep": (SYSTEM_INFO_DIR, "grep", "grep"),
    "sed": (SYSTEM_INFO_DIR, "sed", "sed"),
    "diffutils": (SYSTEM_INFO_DIR, "diffutils", "diffutils"),
    "gzip": (SYSTEM_INFO_DIR, "gzip", "gzip"),
    "wget": (SYSTEM_INFO_DIR, "wget", "wget"),
}
MIN_SECTION_CHARS = 200
# http://archive.ubuntu.com/ubuntu/dists/noble/Contents-amd64.gz (all components)
UBUNTU_CONTENTS = Path(__file__).parent / "raw" / "ubuntu" / "Contents-noble-amd64.gz"
_CODE = re.compile(r"<code>(.*?)</code>", re.S)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


# ---------- man pages ----------

def answer_commands(rec: dict) -> set[str]:
    """Names used in command position in the code blocks of a record's accepted answer."""
    names: set[str] = set()
    for block in _CODE.findall(rec["answer_html"]):
        for line in html.unescape(block).splitlines():
            names.update(command_names(line.lstrip("$# ")))
    return names


def document_frequencies(history: Iterable[dict]) -> tuple[Counter[str], Counter[str]]:
    """(posts using each name as a command, posts using it as a command or a tag)."""
    commands_df: Counter[str] = Counter()
    names_df: Counter[str] = Counter()
    for rec in history:
        cmds = answer_commands(rec)
        commands_df.update(cmds)
        names_df.update(cmds | set(rec["tags"]))
    return commands_df, names_df


def man_paths(name: str) -> list[str]:
    out = subprocess.run(["man", "-aw", "-s", MAN_SECTIONS, name], text=True, capture_output=True,
                         env={**os.environ, "LANG": "C"}).stdout
    return [p for p in out.split() if "/man/man" in p]  # English pages only


def render_man(path: str) -> str:
    env = {**os.environ, "MANWIDTH": "100", "GROFF_NO_SGR": "1", "LANG": "C.UTF-8", "MANPAGER": "cat"}
    out = subprocess.run(["man", "--no-hyphenation", "--no-justification", "-l", path],
                         text=True, capture_output=True, env=env, timeout=60).stdout
    return _ANSI.sub("", re.sub(r".\x08", "", out)).strip()


def dpkg_packages(paths: list[str]) -> dict[str, str]:
    out = subprocess.run(["dpkg", "-S", *paths], text=True, capture_output=True).stdout
    owners: dict[str, str] = {}
    for line in out.splitlines():
        pkg, _, path = line.partition(": ")
        owners[path.strip()] = pkg.split(":")[0]
    return owners


def os_codename() -> str:
    for line in Path("/etc/os-release").read_text().splitlines():
        if line.startswith("VERSION_CODENAME="):
            return line.split("=", 1)[1].strip('"')
    return "noble"


def man_docs(names: list[str]) -> Iterator[dict]:
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = {os.path.realpath(p) for ps in pool.map(man_paths, names) for p in ps}
    paths_sorted = sorted(paths)
    owners = dpkg_packages(paths_sorted)
    codename = os_codename()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for path, text in zip(paths_sorted, pool.map(render_man, paths_sorted)):
            m = re.search(r"/man(\w+)/([^/]+)\.\1[^/]*$", path)
            if not m or len(text) < MIN_SECTION_CHARS:
                continue
            section, name = m.group(1), m.group(2)
            yield {
                "doc_id": f"man:{name}({section})",
                "source": "man",
                "title": f"{name}({section}) man page",
                "url": f"https://manpages.ubuntu.com/manpages/{codename}/en/man{section}/{name}.{section}.html",
                "package": owners.get(path, "unknown"),
                "text": text,
            }


# ---------- GNU manuals ----------

def texinfo_html_name(node: str) -> str:
    """Texinfo's HTML cross-reference node-name expansion (letters/digits kept, space -> '-')."""
    node = " ".join(node.split())
    return "".join(c if c.isascii() and c.isalnum() else "-" if c == " " else f"_{ord(c):04x}" for c in node)


def info_nodes(directory: Path, stem: str) -> Iterator[tuple[str, str]]:
    """(node name, text) pairs from a (possibly split) gzipped Info manual."""
    parts = sorted(directory.glob(f"{stem}.info-*.gz"), key=lambda p: int(p.name.split("-")[-1][:-3]))
    for part in parts or [directory / f"{stem}.info.gz"]:
        with gzip.open(part, "rt", encoding="utf-8", errors="replace") as f:
            for chunk in f.read().split("\x1f"):
                header, _, body = chunk.strip("\n").partition("\n")
                m = re.search(r"Node: ([^,\t]+)", header)
                if m:
                    yield m.group(1).strip(), body.strip()


def info_docs() -> Iterator[dict]:
    for stem, (directory, project, package) in INFO_MANUALS.items():
        if not (directory / f"{stem}.info.gz").exists():
            sys.exit(f"missing Info manual {directory / stem}.info.gz (see data/SOURCES.md)")
        for node, text in info_nodes(directory, stem):
            if len(text) < MIN_SECTION_CHARS or node.lower().endswith("index") or node == "Top":
                continue
            yield {
                "doc_id": f"gnu:{project}#{node}",
                "source": "gnu",
                "title": f"GNU {project} manual: {node}",
                "url": f"https://www.gnu.org/software/{project}/manual/html_node/{texinfo_html_name(node)}.html",
                "package": package,
                "text": text,
            }


def dedupe(docs: list[dict]) -> list[dict]:
    """Drop docs whose text or doc_id repeats an earlier one (e.g. gcc-13 vs g++-13 man pages,
    or the same page installed under two paths). doc_ids must be unique: they seed point IDs."""
    seen: set[str] = set()
    kept = []
    for d in docs:
        digest = hashlib.sha1(d["text"].encode()).hexdigest()
        if digest not in seen and d["doc_id"] not in seen:
            seen.update((digest, d["doc_id"]))
            kept.append(d)
    return kept


def select_frozen(docs: list[dict], frozen: set[str]) -> list[dict]:
    """Keep exactly the man pages listed in data/man_pages.json, so tweaks to the command parser
    (which drives candidate selection) never silently change the corpus behind earlier runs."""
    kept = [d for d in docs if d["doc_id"] in frozen]
    missing = frozen - {d["doc_id"] for d in kept}
    if missing:
        sys.exit(f"{len(missing)} frozen man pages not produced (e.g. {sorted(missing)[:5]}); "
                 "use --refresh-man-selection if the corpus is meant to change")
    return kept


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa", type=Path, default=PROCESSED_DIR / "unix_qa.jsonl")
    parser.add_argument("--out-dir", type=Path, default=PROCESSED_DIR)
    parser.add_argument("--refresh-man-selection", action="store_true",
                        help="re-derive data/man_pages.json (changes the corpus; re-run baselines after)")
    args = parser.parse_args()

    with args.qa.open() as f:
        history = [r for r in map(json.loads, f) if r["created"] < HISTORY_CUTOFF]
    commands_df, df = document_frequencies(history)
    names = sorted(n for n, c in df.items() if c >= MIN_DOC_FREQ and re.fullmatch(r"[\w.+-]+", n))
    print(f"{len(names)} candidate names from {len(history)} history posts")

    man = dedupe(list(man_docs(names)))
    if args.refresh_man_selection or not MAN_SELECTION.exists():
        MAN_SELECTION.write_text(json.dumps(sorted(d["doc_id"] for d in man), indent=0))
    man = select_frozen(man, set(json.loads(MAN_SELECTION.read_text())))
    docs = dedupe(man + list(info_docs()))
    with (args.out_dir / "docs.jsonl").open("w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    # Tools from other distros (emerge, nixos-option, ...) that past accepted answers use regularly.
    community = {n for n, c in commands_df.items()
                 if c >= MIN_DOC_FREQ and re.fullmatch(r"[A-Za-z0-9][\w.+-]*", n)}
    known = sorted(collect_known_commands(UBUNTU_CONTENTS, extra=community))
    (args.out_dir / "known_commands.json").write_text(json.dumps(known))

    stats = {
        "candidate_names": len(names),
        "docs_by_source": Counter(d["source"] for d in docs),
        "chars_by_source": {s: sum(len(d["text"]) for d in docs if d["source"] == s) for s in ("man", "gnu")},
        "packages": Counter(d["package"] for d in docs).most_common(),
        "known_commands": len(known),
    }
    (args.out_dir / "docs_stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps({k: v for k, v in stats.items() if k != "packages"}, indent=2))


if __name__ == "__main__":
    main()
