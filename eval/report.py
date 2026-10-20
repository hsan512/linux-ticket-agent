"""Aggregate a run into metrics.json + report.md, and export a hand spot-check sheet.

If spot_check.csv already has `human_correct` filled in (1/0), judge-vs-human agreement is reported.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import Counter
from statistics import mean, median

from agent.config import PROCESSED_DIR, RUNS_DIR
from agent.tools.command_check import check_answer
from eval.common import latest_by_question, load_jsonl, load_known_commands
from eval.judge import cited_ids

SPOT_CHECK_N = 30
SPOT_CHECK_SEED = 7


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (centre - half, centre + half)


def percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))] if s else 0.0


def cohen_kappa(a: list[int], b: list[int]) -> float:
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def command_metrics(preds: list[dict], known: set[str], items: dict[int, dict]) -> dict:
    """Re-check every proposed command with the current known-command set, so all runs are
    measured the same way."""
    per_answer = [check_answer(p["draft"]["commands"], known, ticket_of(items[p["question_id"]])) for p in preds]
    checks = [c for answer in per_answer for c in answer]
    with_cmds = [answer for answer in per_answer if answer]
    return {
        "answers_with_commands": len(with_cmds),
        "commands_total": len(checks),
        "syntax_error_rate": sum(bool(c.syntax_error) for c in checks) / len(checks) if checks else 0.0,
        "unknown_command_rate": sum(bool(c.unknown_names) for c in checks) / len(checks) if checks else 0.0,
        "answers_with_invalid_command_rate":
            sum(any(not c.ok for c in answer) for answer in with_cmds) / len(with_cmds) if with_cmds else 0.0,
        "unknown_names_top": Counter(n for c in checks for n in c.unknown_names).most_common(15),
    }


def distribution(values: list[float]) -> dict:
    return {"mean": mean(values), "p50": median(values), "p95": percentile(values, 0.95), "max": max(values)}


def latency_metrics(preds: list[dict]) -> dict:
    """Distribution of total latency plus the median of each recorded stage (retrieval, llm, ...)."""
    stages = sorted({k for p in preds for k in p["latency_s"]} - {"total"})
    return {"total": distribution([p["latency_s"]["total"] for p in preds]),
            "stage_p50": {s: median(p["latency_s"].get(s, 0.0) for p in preds) for s in stages}}


def cost_metrics(preds: list[dict], judgments: list[dict], n_correct: int) -> dict:
    """Serving cost (generation + query embeddings; judge cost is evaluation overhead, reported apart).
    per_correct = what each correct answer costs, the trade-off number across systems."""
    generation_total = sum(p["cost_usd"] for p in preds)
    return {"per_ticket": distribution([p["cost_usd"] for p in preds]),
            "per_correct": generation_total / n_correct if n_correct else None,
            "generation_total": generation_total,
            "judge_total": sum(j["cost_usd"] for j in judgments)}


def selective_metrics(preds: dict[int, dict], judgments: dict[int, dict]) -> dict:
    """Escalated tickets are not shown to the user. Systems without escalation answer everything
    (coverage 100%), so all runs are comparable on these numbers."""
    qs = [q for q in judgments if q in preds]
    answered = [q for q in qs if preds[q].get("status") != "escalated"]
    correct = sum(judgments[q]["correct"] for q in answered)
    return {
        "coverage": len(answered) / len(qs) if qs else 0.0,
        "accuracy_on_answered": correct / len(answered) if answered else 0.0,
        "accuracy_on_answered_ci95": list(wilson_ci(correct, len(answered))),
        "wrong_answers_shown": len(answered) - correct,
        "correct_answers_escalated": sum(judgments[q]["correct"] for q in qs if q not in answered),
    }


def ticket_of(item: dict) -> str:
    return f"{item['title']}\n\n{item['question_text']}"


def citation_metrics(preds: list[dict]) -> dict:
    cited = [p for p in preds if cited_ids(p)]
    all_ids = [c for p in preds for c in p["draft"]["citations"]]
    dangling = [c for p in preds for c in p["draft"]["citations"] if c not in p["evidence_text"]]
    return {
        "answers_with_citations_rate": len(cited) / len(preds) if preds else 0.0,
        "dangling_citation_rate": len(dangling) / len(all_ids) if all_ids else 0.0,
        "cited_sources": Counter(c[0] for c in all_ids),
    }


def agreement(rows: list[dict], col: str, judgments: dict[int, dict]) -> dict | None:
    """Agreement between the judge's correct/incorrect and the labels in column `col`."""
    labelled = [r for r in rows if r[col].strip() in {"0", "1"}]
    if not labelled:
        return None
    other = [int(r[col]) for r in labelled]
    judge = [int(judgments[int(r["question_id"])]["correct"]) for r in labelled]
    return {"n": len(labelled), "agreement": sum(a == b for a, b in zip(other, judge)) / len(labelled),
            "cohen_kappa": cohen_kappa(other, judge),
            "disagreements": [int(r["question_id"]) for r, a, b in zip(labelled, other, judge) if a != b]}


def spot_check(run_dir, preds: dict[int, dict], judgments: dict[int, dict], items: dict[int, dict]) -> dict | None:
    path = run_dir / "spot_check.csv"
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        # human_correct: hand labels. claude_correct: a second LLM grader (Claude), NOT a human.
        return {col: agreement(rows, col, judgments) for col in ("human_correct", "claude_correct")
                if col in rows[0]}
    ids = sorted(judgments)
    sample = random.Random(SPOT_CHECK_SEED).sample(ids, min(SPOT_CHECK_N, len(ids)))
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["question_id", "url", "judge_score", "judge_correct", "human_correct", "human_notes",
                    "question", "reference_answer", "candidate_answer", "judge_reasoning"])
        for qid in sample:
            item, j = items[qid], judgments[qid]
            w.writerow([qid, item["url"], j["score"], int(j["correct"]), "", "",
                        f"{item['title']}\n\n{item['question_text']}", item["answer_text"],
                        preds[qid]["draft"]["answer"], j["correctness_reasoning"]])
    print(f"Wrote {path}: fill human_correct (1/0) and re-run report.py for agreement")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    run_dir = RUNS_DIR / args.run_id

    all_preds = latest_by_question(load_jsonl(run_dir / "predictions.jsonl"))
    preds = {q: p for q, p in all_preds.items() if p["error"] is None}
    judgments = {q: j for q, j in latest_by_question(load_jsonl(run_dir / "judgments.jsonl")).items()
                 if j["error"] is None and q in preds}
    items = {r["question_id"]: r for r in load_jsonl(PROCESSED_DIR / "benchmark.jsonl")}
    config = json.loads((run_dir / "config.json").read_text())
    n_split = sum(1 for r in items.values() if r["split"] == config["split"])
    p_list, j_list = list(preds.values()), list(judgments.values())

    n_correct = sum(j["correct"] for j in j_list)
    lo, hi = wilson_ci(n_correct, len(j_list))
    metrics = {
        "run_id": args.run_id,
        "config": {k: v for k, v in config.items() if k != "prompt"},
        "tickets_in_split": n_split,
        "tickets_answered": len(preds),
        "generation_errors": len(all_preds) - len(preds),
        "tickets_judged": len(j_list),
        "accuracy": n_correct / len(j_list) if j_list else 0.0,
        "accuracy_ci95": [lo, hi],
        "mean_score": mean(j["score"] for j in j_list) if j_list else 0.0,
        "score_hist": dict(sorted(Counter(j["score"] for j in j_list).items())),
        "faithfulness": dict(Counter(j["faithfulness"] for j in j_list)),
        "selective": selective_metrics(preds, judgments),
        "commands": command_metrics(p_list, load_known_commands(), items),
        "citations": citation_metrics(p_list),
        "latency_s": latency_metrics(p_list),
        "tokens_per_ticket": {"input": mean(p["usage"]["input_tokens"] for p in p_list),
                              "output": mean(p["usage"]["output_tokens"] for p in p_list)},
        "llm_calls_per_ticket": distribution([p["usage"]["llm_calls"] for p in p_list]),
        "cost_usd": cost_metrics(p_list, j_list, n_correct),
        "spot_check": spot_check(run_dir, preds, judgments, items),
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (run_dir / "report.md").write_text(render_markdown(metrics))
    print((run_dir / "report.md").read_text())


def render_markdown(m: dict) -> str:
    c, cit, cost = m["commands"], m["citations"], m["cost_usd"]
    lat, calls, per = m["latency_s"]["total"], m["llm_calls_per_ticket"], cost["per_ticket"]
    sel = m["selective"]
    lo, hi = m["accuracy_ci95"]
    spot = m["spot_check"]
    lines = [
        f"# Run `{m['run_id']}` ({m['config']['system']}, split={m['config']['split']})",
        "",
        f"Generator `{m['config']['generator']}`; workers {m['config'].get('workers', '?')}; tickets answered {m['tickets_answered']}/{m['tickets_in_split']}"
        f" (errors: {m['generation_errors']}); judged {m['tickets_judged']}.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Accuracy, all tickets (judge score >= 4) | {m['accuracy']:.1%} (95% CI {lo:.1%}-{hi:.1%}) |",
        f"| Coverage (answered, not escalated) | {sel['coverage']:.1%} |",
        f"| Accuracy on answered tickets | {sel['accuracy_on_answered']:.1%} "
        f"(95% CI {sel['accuracy_on_answered_ci95'][0]:.1%}-{sel['accuracy_on_answered_ci95'][1]:.1%}) |",
        f"| Wrong answers shown / correct answers escalated | {sel['wrong_answers_shown']} / "
        f"{sel['correct_answers_escalated']} |",
        f"| Mean judge score (1-5) | {m['mean_score']:.2f} |",
        f"| Score histogram | {m['score_hist']} |",
        f"| Faithfulness of cited answers | {m['faithfulness']} |",
        f"| Answers citing a passage | {cit['answers_with_citations_rate']:.1%} |",
        f"| Dangling citations (ID not retrieved) | {cit['dangling_citation_rate']:.1%} |",
        f"| Commands proposed | {c['commands_total']} in {c['answers_with_commands']} answers |",
        f"| Commands with syntax errors (bash -n) | {c['syntax_error_rate']:.1%} |",
        f"| Commands with unknown names | {c['unknown_command_rate']:.1%} |",
        f"| Answers with >= 1 invalid command | {c['answers_with_invalid_command_rate']:.1%} |",
        f"| Latency p50 / p95 / max | {lat['p50']:.1f}s / {lat['p95']:.1f}s / {lat['max']:.1f}s |",
        f"| Latency p50 by stage | {', '.join(f'{k} {v:.2f}s' for k, v in m['latency_s']['stage_p50'].items())} |",
        f"| Tokens per ticket (in / out) | {m['tokens_per_ticket']['input']:.0f} / {m['tokens_per_ticket']['output']:.0f} |",
        f"| LLM calls per ticket (mean / max) | {calls['mean']:.1f} / {calls['max']} |",
        f"| Cost per ticket (mean / p95 / max) | ${per['mean']:.4f} / ${per['p95']:.4f} / ${per['max']:.4f} |",
        f"| Cost per correct answer | {'$%.4f' % cost['per_correct'] if cost['per_correct'] else 'n/a'} |",
        f"| Cost total (generation / judge) | ${cost['generation_total']:.2f} / ${cost['judge_total']:.2f} |",
        "",
        f"Most frequent unknown command names: {c['unknown_names_top']}",
        "",
        "Judge vs. human spot check: " + fmt_agreement((spot or {}).get("human_correct")),
        "",
        "Judge vs. second LLM grader (Claude, not a human): " + fmt_agreement((spot or {}).get("claude_correct")),
        "",
    ]
    return "\n".join(lines)


def fmt_agreement(a: dict | None) -> str:
    if not a:
        return "not filled in yet (spot_check.csv)"
    return (f"n={a['n']}, agreement {a['agreement']:.1%}, Cohen's kappa {a['cohen_kappa']:.2f}, "
            f"disagreements {a['disagreements']}")


if __name__ == "__main__":
    main()
