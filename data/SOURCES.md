# Data sources and licenses

## Unix & Linux Stack Exchange (questions + accepted answers)

- **Site:** [Unix & Linux Stack Exchange](https://unix.stackexchange.com)
- **Release:** Stack Exchange data dump, data up to and including 2025-12-31
- **Downloaded from:** community release on the Internet Archive,
  <https://archive.org/details/stackexchange_20251231>, file `unix.stackexchange.com.7z`
  (753,598,550 bytes, MD5 `2b0958a9d28dacf0484d98d88addd7cb`, verified by `download.py`)
- **Files used:** `Posts.xml`, `Users.xml` (only for author display names)

### License

Content is © its authors and editors, licensed under CC BY-SA. The version depends on when the
post was contributed (the per-post `ContentLicense` field is kept in every record):

| Contributed | License |
|---|---|
| before 2011-04-08 | [CC BY-SA 2.5](https://creativecommons.org/licenses/by-sa/2.5/) |
| 2011-04-08 to 2018-05-01 | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) |
| after 2018-05-01 | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |

Source: `LICENSE.txt` in the release and <https://stackoverflow.com/help/licensing>.

### Additional terms

Stack Exchange's own download page asks users to agree to use the dump only for their own
projects and not to train large language models. This project uses the data only for
retrieval and offline evaluation. It does **not** train or fine-tune any model on it, and it
does not redistribute the dump. Files derived from it (`data/processed/`) are git-ignored; the
one committed file there, the command -> package table, holds no Stack Exchange content.

### Attribution

Every record in `data/processed/unix_qa.jsonl` carries the question and answer URLs, author
names and profile links, and license version. Anything shown to users (demo output, reports)
must link the original post and name the author and Unix & Linux Stack Exchange.

### Data integrity (watermarking)

Starting with the 2025-06-30 dump, Stack Exchange inserted fabricated "watermark" posts with
fixed IDs (`1000000001`, `1000000010`; see the
[community-compiled list](https://github.com/LunarWatcher/se-data-dump-transformer/blob/master/docs/meta/Known%20watermarks.md)).
The release README says the 2025-12-31 dump does not appear to contain them. As a precaution,
`build_qa.py` drops every post with `Id >= 1,000,000,000` (real Unix & Linux IDs are far lower)
and reports the count in `stats.json`.

## Filtering (`build_qa.py`)

A question is kept only if it is a question post that:

- is not a watermark row,
- has an accepted answer,
- is not closed,
- has a score of at least 1.

Its accepted answer must also have a score of at least 1. The counts for each rejection reason
are written to `data/processed/stats.json`.

## Documentation corpus (`build_docs.py`)

Built offline from documentation shipped with Ubuntu 24.04 (noble). Kept locally only
(`data/processed/docs.jsonl`, git-ignored); citations link to the public online copies.

| Source | What | License |
|---|---|---|
| Man pages, sections 1/5/7/8 | Pages for command names and tags that appear in at least 5 *history* posts (before 2023-01-01) and are installed locally. 748 pages after removing identical duplicates. | Per package. The owning Debian package is stored per document; see `/usr/share/doc/<package>/copyright`. Mostly GPL, BSD, MIT, and the Linux man-pages license. |
| GNU manuals (Info): coreutils, findutils, grep, sed, diffutils, gzip, wget | Installed system Info files, one document per node (index nodes dropped) | GFDL (see each package's copyright file) |
| GNU manuals (Info): Bash Reference Manual, GAWK manual | Ubuntu packages `bash-doc` 5.2.21-2ubuntu4 and `gawk-doc` 5.2.1-1, fetched with `apt-get download` and unpacked with `dpkg-deb -x` into `data/raw/gnu/extracted/` (gnu.org timed out) | bash-doc: GPL-3+; gawk-doc: GFDL-1.3+ |

**Known-command list** (`known_commands.json`, used to flag nonexistent commands): executable and
bash-completion names from Ubuntu's package index
`http://archive.ubuntu.com/ubuntu/dists/noble/Contents-amd64.gz` (51,301,092 bytes, SHA-256
`c8718dbbacd1ab72675513cf0674ff9921fcf781d9f49c4c0eaf68a49c18adc1`, downloaded 2026-10-19), plus
the local man database, PATH, bash and zsh builtins, and names used as commands in at least 5
pre-2023 accepted answers (covers other distros' tools such as `emerge`). File names only; no
package content. At check time, names that appear in the ticket itself and functions defined by
the answer are also accepted. Remaining "unknown" names are an upper bound on invented commands
(e.g. NixOS tools still count as unknown).

**Command -> package table** (`command_packages.json`, used by the agent's `CheckCommand` tool
and by `validate`): built by `data/build_packages.py` from the same noble Contents index.
Executable names directly in `bin`, `sbin`, `usr/bin`, `usr/sbin` (hidden files dropped) mapped
to the packages that ship them; 49,682 commands. Package and file names only; no package content
and nothing from Stack Exchange. Unlike the rest of `data/processed/`, this file is committed
(2.5 MB): the API loads it at startup, so the Docker image and CI need it in a fresh clone.
Rebuild it with `python data/build_packages.py`.

The man page selection is frozen in `data/man_pages.json` (743 pages) so the corpus behind
recorded runs cannot drift. Re-derive it only with `build_docs.py --refresh-man-selection`, and
re-run baselines afterwards.

Man pages are selected using the history split only, so benchmark questions do not influence
what documentation is available.

## Similar-issues corpus

Unix & Linux questions created **before 2023-01-01** (72,795 pairs), embedded on title + question.
The benchmark is drawn from questions created on or after that date, so its accepted answers
are never retrievable.

## Benchmark (`eval/make_benchmark.py`)

200 questions (40 dev, 160 test) sampled from the 2,984 pairs created on or after 2023-01-01
whose accepted answer contains code, whose question has no image, and whose question and answer
are at most 4,000 and 6,000 characters. Sampling is stratified by primary tag (seed 5106).
Only the IDs and criteria are committed (`eval/benchmark/benchmark_ids.json`).

## Reproduce

```bash
conda env create -f environment.yml
conda activate ticket-agent
python data/download.py      # ~754 MB download, verified, extracts Posts.xml + Users.xml
python data/build_qa.py      # writes data/processed/unix_qa.jsonl and stats.json
(cd data/raw/gnu && apt-get download bash-doc gawk-doc && for d in *.deb; do dpkg-deb -x "$d" extracted; done)
(mkdir -p data/raw/ubuntu && curl -o data/raw/ubuntu/Contents-noble-amd64.gz http://archive.ubuntu.com/ubuntu/dists/noble/Contents-amd64.gz)
python data/build_docs.py    # writes data/processed/docs.jsonl, known_commands.json
docker compose up -d         # Qdrant on localhost:6333
PYTHONPATH=. python data/build_index.py         # dry run: prints token count and cost
PYTHONPATH=. python data/build_index.py --yes   # embeds and uploads
PYTHONPATH=. python eval/make_benchmark.py
```
