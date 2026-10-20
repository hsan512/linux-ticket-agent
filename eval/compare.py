"""Side-by-side comparison of runs on the same split: accuracy vs. cost vs. latency.

Accuracy differences are tested per ticket against a reference run with an exact McNemar test
(the systems answer the same tickets, so the comparison is paired).

    python -m eval.compare --ref baseline-test llm-only-test baseline-test agent-test
"""

from __future__ import annotations

import argparse
import json
from math import comb

from agent.config import PROCESSED_DIR, RUNS_DIR
from eval.common import latest_by_question, load_jsonl
from eval.report import wilson_ci


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value; b, c = tickets only one of the two systems got right."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


def correct_by_question(run_id: str) -> dict[int, bool]:
    judged = latest_by_question(load_jsonl(RUNS_DIR / run_id / "judgments.jsonl"))
    return {q: j["correct"] for q, j in judged.items() if j["error"] is None}


def paired(ref: dict[int, bool], other: dict[int, bool]) -> dict:
    common = ref.keys() & other.keys()
    only_other = sum(other[q] and not ref[q] for q in common)
    only_ref = sum(ref[q] and not other[q] for q in common)
    return {"n": len(common), "wins": only_other, "losses": only_ref, "p_value": mcnemar_exact(only_other, only_ref)}


def row(run_id: str, ref_id: str) -> str:
    m = json.loads((RUNS_DIR / run_id / "metrics.json").read_text())
    lat, cost = m["latency_s"]["total"], m["cost_usd"]
    lo, hi = m["accuracy_ci95"]
    per_correct = f"${cost['per_correct']:.4f}" if cost["per_correct"] else "n/a"
    if run_id == ref_id:
        vs_ref = "reference"
    else:
        p = paired(correct_by_question(ref_id), correct_by_question(run_id))
        vs_ref = f"+{p['wins']} / -{p['losses']} (p={p['p_value']:.3f})"
    return (f"| `{run_id}` | {m['config']['system']} | {m['accuracy']:.1%} ({lo:.0%}-{hi:.0%}) | {vs_ref} "
            f"| {m['commands']['answers_with_invalid_command_rate']:.1%} "
            f"| ${cost['per_ticket']['mean']:.4f} | ${cost['per_ticket']['p95']:.4f} | {per_correct} "
            f"| {lat['p50']:.1f}s / {lat['p95']:.1f}s | {m['llm_calls_per_ticket']['mean']:.1f} "
            f"| {m['tokens_per_ticket']['input']:.0f} / {m['tokens_per_ticket']['output']:.0f} |")


def subset_table(runs: list[str], ref_id: str, since: str) -> list[str]:
    """Accuracy restricted to questions created on/after `since` (e.g. past the model's likely
    training cutoff, where memorised answers matter less), with the same paired test."""
    items = {r["question_id"]: r for r in load_jsonl(PROCESSED_DIR / "benchmark.jsonl")}
    keep = lambda d: {q: v for q, v in d.items() if items[q]["created"] >= since}  # noqa: E731
    ref = keep(correct_by_question(ref_id))
    lines = [f"Questions created on or after {since} (n = {len(ref)}):", "",
             "| Run | Accuracy (95% CI) | vs ref: wins / losses (McNemar) |", "|---|---|---|"]
    for run_id in runs:
        c = keep(correct_by_question(run_id))
        lo, hi = wilson_ci(sum(c.values()), len(c))
        p = paired(ref, c)
        vs = "reference" if run_id == ref_id else f"+{p['wins']} / -{p['losses']} (p={p['p_value']:.3f})"
        lines.append(f"| `{run_id}` | {sum(c.values()) / len(c):.1%} ({lo:.0%}-{hi:.0%}) | {vs} |")
    return lines


def selective_row(run_id: str) -> str:
    s = json.loads((RUNS_DIR / run_id / "metrics.json").read_text())["selective"]
    lo, hi = s["accuracy_on_answered_ci95"]
    return (f"| `{run_id}` | {s['coverage']:.1%} | {s['accuracy_on_answered']:.1%} ({lo:.0%}-{hi:.0%}) "
            f"| {s['wrong_answers_shown']} | {s['correct_answers_escalated']} |")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--ref", required=True, help="run that the others are tested against")
    parser.add_argument("--since", help="also report accuracy on questions created on/after this date")
    parser.add_argument("--out", help="also write the table to this markdown file")
    args = parser.parse_args()

    lines = [
        "| Run | System | Accuracy (95% CI) | vs ref: wins / losses (McNemar) | Answers w/ invalid cmd "
        "| Cost/ticket | Cost p95 | Cost/correct | Latency p50 / p95 | LLM calls | Tokens in / out |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        *(row(r, args.ref) for r in args.runs),
        "",
        "Cost = serving cost (generation + query embeddings), excluding the judge. Latency measured with "
        "the `workers` concurrency recorded in each run's config; compare runs at the same setting.",
    ]
    lines += ["", "Selective answering (escalated tickets are handed to a human, not shown):", "",
              "| Run | Coverage | Accuracy on answered (95% CI) | Wrong answers shown | Correct answers escalated |",
              "|---|---|---|---|---|", *(selective_row(r) for r in args.runs)]
    if args.since:
        lines += ["", *subset_table(args.runs, args.ref, args.since)]
    table = "\n".join(lines)
    print(table)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(table + "\n")


if __name__ == "__main__":
    main()
