"""Filter the Unix & Linux dump into question/accepted-answer pairs (JSONL).

Each record keeps the attribution CC BY-SA requires: post URL, author, and license version.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from se_dump import ANSWER, QUESTION, html_to_text, is_watermark, iter_rows, parse_tags

SITE_URL = "https://unix.stackexchange.com"
DATA_DIR = Path(__file__).parent


@dataclass(frozen=True)
class FilterConfig:
    min_question_score: int = 1
    min_answer_score: int = 1


def question_reject_reason(row: dict[str, str], cfg: FilterConfig) -> str | None:
    """Return why a row is not a usable question, or None if it should be kept."""
    if row.get("PostTypeId") != QUESTION:
        return "not_question"
    if is_watermark(row):
        return "watermark"
    if not row.get("AcceptedAnswerId"):
        return "no_accepted_answer"
    if row.get("ClosedDate"):
        return "closed"
    if int(row.get("Score", 0)) < cfg.min_question_score:
        return "low_question_score"
    return None


def answer_reject_reason(row: dict[str, str], cfg: FilterConfig) -> str | None:
    if is_watermark(row):
        return "watermark"
    if int(row.get("Score", 0)) < cfg.min_answer_score:
        return "low_answer_score"
    return None


def author_of(row: dict[str, str], users: dict[str, str]) -> dict[str, str | None]:
    user_id = row.get("OwnerUserId")
    name = users.get(user_id) if user_id else None
    return {
        "name": name or row.get("OwnerDisplayName") or "(unknown)",
        "profile_url": f"{SITE_URL}/users/{user_id}" if user_id and user_id != "-1" else None,
    }


def load_display_names(users_xml: Path, wanted: set[str]) -> dict[str, str]:
    return {r["Id"]: r.get("DisplayName", "") for r in iter_rows(users_xml) if r["Id"] in wanted}


def build_record(q: dict[str, str], a: dict[str, str], users: dict[str, str]) -> dict:
    return {
        "question_id": int(q["Id"]),
        "url": f"{SITE_URL}/q/{q['Id']}",
        "title": q.get("Title", ""),
        "tags": parse_tags(q.get("Tags")),
        "created": q.get("CreationDate"),
        "question_score": int(q.get("Score", 0)),
        "view_count": int(q.get("ViewCount", 0)),
        "question_html": q.get("Body", ""),
        "question_text": html_to_text(q.get("Body", "")),
        "question_author": author_of(q, users),
        "question_license": q.get("ContentLicense"),
        "answer_id": int(a["Id"]),
        "answer_url": f"{SITE_URL}/a/{a['Id']}",
        "answer_score": int(a.get("Score", 0)),
        "answer_html": a.get("Body", ""),
        "answer_text": html_to_text(a.get("Body", "")),
        "answer_author": author_of(a, users),
        "answer_license": a.get("ContentLicense"),
        "self_answered": q.get("OwnerUserId") is not None and q.get("OwnerUserId") == a.get("OwnerUserId"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=DATA_DIR / "raw")
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR / "processed")
    args = parser.parse_args()
    cfg = FilterConfig()
    posts_xml = args.raw_dir / "Posts.xml"

    # Pass 1: keep candidate questions, remember which answers we need.
    stats: Counter[str] = Counter()
    questions: dict[str, dict[str, str]] = {}
    for row in iter_rows(posts_xml):
        if row.get("PostTypeId") == ANSWER:
            continue
        reason = question_reject_reason(row, cfg)
        stats[f"question_rejected:{reason}" if reason else "question_kept"] += 1
        if reason is None:
            questions[row["AcceptedAnswerId"]] = row

    # Pass 2: attach accepted answers.
    pairs: list[tuple[dict[str, str], dict[str, str]]] = []
    for row in iter_rows(posts_xml):
        if row.get("PostTypeId") != ANSWER or row["Id"] not in questions:
            continue
        reason = answer_reject_reason(row, cfg)
        stats[f"answer_rejected:{reason}" if reason else "pair_kept"] += 1
        if reason is None:
            pairs.append((questions[row["Id"]], row))
    stats["accepted_answer_missing"] = stats["question_kept"] - sum(
        v for k, v in stats.items() if k.startswith("answer_rejected:") or k == "pair_kept"
    )

    user_ids = {p.get("OwnerUserId") for pair in pairs for p in pair} - {None}
    users = load_display_names(args.raw_dir / "Users.xml", user_ids)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / "unix_qa.jsonl"
    tag_counts: Counter[str] = Counter()
    with out_path.open("w", encoding="utf-8") as f:
        for q, a in sorted(pairs, key=lambda p: int(p[0]["Id"])):
            record = build_record(q, a, users)
            tag_counts.update(record["tags"])
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = {
        "filter_config": vars(cfg),
        "counts": dict(sorted(stats.items())),
        "pairs_written": len(pairs),
        "top_tags": tag_counts.most_common(30),
    }
    (args.out_dir / "stats.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary["counts"], indent=2))
    print(f"Wrote {len(pairs)} pairs to {out_path}")


if __name__ == "__main__":
    main()
