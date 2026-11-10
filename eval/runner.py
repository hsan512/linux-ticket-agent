"""Parallel, resumable, budget-capped execution of per-ticket jobs, appending JSONL
records."""

from __future__ import annotations

import argparse
import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.config import RUNS_DIR
from eval.common import load_jsonl


def add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--split", choices=["dev", "test"], required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--limit", type=int, help="only the first N tickets of the split (smoke test)"
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--budget-usd", type=float, default=3.0)


def prepare_run(args: argparse.Namespace, config: dict) -> Path:
    """Create eval/runs/<run_id>/, write config.json, return the predictions path.
    `workers` is recorded because latency is measured under that much concurrency;
    compare systems only at the same setting."""
    run_dir = RUNS_DIR / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(
        json.dumps(config | {"split": args.split, "workers": args.workers}, indent=2)
    )
    return run_dir / "predictions.jsonl"


def run_jobs(
    jobs: list[dict],
    work: Callable[[dict], dict],
    out_path: Path,
    budget_usd: float,
    workers: int,
    describe: Callable[[dict], str] = lambda rec: "ok",
) -> None:
    """Run `work(job)` for each job whose question_id has no successful record in
    out_path yet.

    Each record must carry question_id, cost_usd, and error (None on success). A job
    that raises is recorded with its error (and retried on the next run), never dropped.
    New jobs stop being submitted once the spend recorded in out_path reaches
    budget_usd.
    """
    existing = load_jsonl(out_path)
    done = {r["question_id"] for r in existing if r["error"] is None}
    spent = sum(r["cost_usd"] for r in existing)
    todo = [j for j in jobs if j["question_id"] not in done]
    print(f"{len(done)} already done, {len(todo)} to run, ${spent:.3f} spent so far")
    lock = threading.Lock()

    def run_one(job: dict) -> None:
        nonlocal spent
        with lock:
            if spent >= budget_usd:
                return
        try:
            rec = work(job)
        except Exception as exc:
            rec = {
                "question_id": job["question_id"],
                "split": job.get("split"),
                "error": repr(exc),
                "cost_usd": 0.0,
            }
        with lock:
            spent += rec["cost_usd"]
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            status = f"ERROR {rec['error']}" if rec["error"] else describe(rec)
            print(f"q{job['question_id']}: {status} (total ${spent:.3f})")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(run_one, todo))
    if spent >= budget_usd:
        print(
            f"Budget ${budget_usd} reached; re-run with a higher --budget-usd to "
            f"continue."
        )
