"""Print a run's answers for reading: each ticket with its issues, revisions, and final answer."""

from __future__ import annotations

import argparse
import json

from agent.config import PROCESSED_DIR, RUNS_DIR
from eval.common import load_benchmark, load_jsonl

RULE = "=" * 80


def show(record: dict, title: str) -> None:
    print(f"{RULE}\nq{record['question_id']}: {title}")
    print(f"status={record['status']}, confidence={record['draft']['confidence']}, "
          f"revisions={record['revise_attempts']}")
    for i, revision in enumerate(record.get("revisions", []), start=1):
        print(f"\n--- before revision {i}: commands")
        print("\n".join(revision["draft"]["commands"]))
        print("--- issues sent to the reviser")
        print("\n".join(f"{issue['kind']}: {issue['message']}" for issue in revision["issues"]))
    if record["validation"]:
        print("\n--- issues in the last draft")
        print("\n".join(f"{issue['kind']}: {issue['message']}" for issue in record["validation"]))
    if record["status"] == "escalated":
        print(f"\n--- escalated, not shown to the asker: {record['escalation_reason']}")
    print(f"\n--- answer\n{record['final_answer'] or record['draft']['answer']}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--id", type=int, help="only this question")
    parser.add_argument("--revised", action="store_true", help="only tickets that were revised or have issues")
    args = parser.parse_args()

    run_dir = RUNS_DIR / args.run_id
    split = json.loads((run_dir / "config.json").read_text())["split"]
    titles = {item["question_id"]: item["title"] for item in load_benchmark(PROCESSED_DIR / "benchmark.jsonl", split)}
    for record in load_jsonl(run_dir / "predictions.jsonl"):
        if args.id and record["question_id"] != args.id:
            continue
        if args.revised and not (record.get("revisions") or record["validation"]):
            continue
        show(record, titles[record["question_id"]])


if __name__ == "__main__":
    main()
