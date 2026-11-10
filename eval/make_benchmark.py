"""Sample the benchmark: questions created on/after HISTORY_CUTOFF, stratified by
primary tag.

Writes eval/benchmark/benchmark_ids.json (committed: IDs + selection criteria only) and
data/processed/benchmark.jsonl (full CC BY-SA records, git-ignored, regenerated from the
IDs).
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict

from agent.config import BENCHMARK_DIR, HISTORY_CUTOFF, PROCESSED_DIR

SEED = 5106
TOP_TAGS = 15
MAX_QUESTION_CHARS = 4000
MAX_ANSWER_CHARS = 6000
CRITERIA = {
    "created_on_or_after": HISTORY_CUTOFF,
    "accepted_answer_contains_code": True,
    "question_has_no_image": True,
    "max_question_chars": MAX_QUESTION_CHARS,
    "max_answer_chars": MAX_ANSWER_CHARS,
}


def is_candidate(rec: dict) -> bool:
    return (
        rec["created"] >= HISTORY_CUTOFF
        and "<code>" in rec["answer_html"]
        and "<img" not in rec["question_html"]
        and len(rec["question_text"]) <= MAX_QUESTION_CHARS
        and len(rec["answer_text"]) <= MAX_ANSWER_CHARS
    )


def primary_tag(tags: list[str], top: set[str]) -> str:
    """The first listed tag that is one of the pool's most common tags, else 'other'."""
    return next((t for t in tags if t in top), "other")


def proportional_allocation(sizes: dict[str, int], total: int) -> dict[str, int]:
    """Largest-remainder allocation of `total` across strata proportional to their
    sizes."""
    pool = sum(sizes.values())
    exact = {k: total * v / pool for k, v in sizes.items()}
    alloc = {k: int(x) for k, x in exact.items()}
    by_remainder = sorted(sizes, key=lambda k: exact[k] - alloc[k], reverse=True)
    for k in by_remainder[: total - sum(alloc.values())]:
        alloc[k] += 1
    return alloc


def stratified_sample(
    pool: list[dict], n: int, n_dev: int, rng: random.Random
) -> tuple[list[dict], list[dict]]:
    tag_counts = Counter(t for r in pool for t in r["tags"])
    top = {t for t, _ in tag_counts.most_common(TOP_TAGS)}
    strata: dict[str, list[dict]] = defaultdict(list)
    for rec in pool:
        strata[primary_tag(rec["tags"], top)].append(rec)

    alloc = proportional_allocation({k: len(v) for k, v in strata.items()}, n)
    dev_alloc = proportional_allocation(alloc, n_dev)
    dev, test = [], []
    for key in sorted(strata):
        chosen = rng.sample(
            sorted(strata[key], key=lambda r: r["question_id"]), alloc[key]
        )
        for rec in chosen:
            rec["stratum"] = key
        dev += chosen[: dev_alloc[key]]
        test += chosen[dev_alloc[key] :]
    return dev, test


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--n-dev", type=int, default=40)
    args = parser.parse_args()

    with (PROCESSED_DIR / "unix_qa.jsonl").open() as f:
        pool = [r for r in map(json.loads, f) if is_candidate(r)]
    dev, test = stratified_sample(pool, args.n, args.n_dev, random.Random(SEED))

    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        "seed": SEED,
        "criteria": CRITERIA,
        "candidate_pool": len(pool),
        "strata": dict(Counter(r["stratum"] for r in dev + test).most_common()),
        "dev": sorted(r["question_id"] for r in dev),
        "test": sorted(r["question_id"] for r in test),
    }
    (BENCHMARK_DIR / "benchmark_ids.json").write_text(json.dumps(manifest, indent=2))
    with (PROCESSED_DIR / "benchmark.jsonl").open("w", encoding="utf-8") as f:
        for split, recs in (("dev", dev), ("test", test)):
            for rec in sorted(recs, key=lambda r: r["question_id"]):
                f.write(json.dumps(rec | {"split": split}, ensure_ascii=False) + "\n")
    print(f"pool={len(pool)} dev={len(dev)} test={len(test)}")
    print(json.dumps(manifest["strata"], indent=2))


if __name__ == "__main__":
    main()
